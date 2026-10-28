"""Resumable, hash-verified GRP acquisition / decode / computation jobs."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from .common import read_json, write_json, file_hash, content_hash, resolved, safe_id
from .geometry import decode_grp, export_mesh, build_cpu_layers
from .cpu_model import recalculate_layers


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, root, config):
        self.root, self.config = Path(root).resolve(), config
        self.work = resolved(root, config['paths']['work'])
        self.path = self.work / 'jobs.json'
        self.data = read_json(self.path) if self.path.exists() else {'schema_version': 1, 'created_at': utc_now(), 'jobs': {}}

    def save(self):
        write_json(self.path, self.data)

    def update(self, digest, **fields):
        job = self.data['jobs'].setdefault(digest, {'id': digest, 'grp_sha256': digest, 'events': []})
        old_status = job.get('status')
        job.update(fields)
        if fields.get('status') != old_status:
            job['events'].append({'at': utc_now(), 'from': old_status, 'to': fields.get('status')})
        self.save()
        return job

    def overview(self):
        counts = {}
        for job in self.data['jobs'].values(): counts[job['status']] = counts.get(job['status'], 0) + 1
        return {'schema_version': 1, 'jobs': len(self.data['jobs']), 'status_counts': counts,
                'profile_issues': self.data.get('profile_issues', []),
                'items': [{key: job.get(key) for key in ['id', 'vehicle_id', 'status', 'error', 'mesh_resources', 'geometry_reused', 'compute_cache_reused', 'results', 'physical_cache_key']} for job in self.data['jobs'].values()]}


def _validated_url(url, source):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ['https', 'http'] or parsed.username or parsed.password:
        raise ValueError('Only credential-free HTTP(S) sources are supported')
    if parsed.hostname not in source['allowed_hosts']:
        raise ValueError('Source hostname is not in explicitly configured allowed_hosts')
    return url


def _download(url, dest, expected, config, manifest=False):
    source, policy = config['source'], config['acquisition']
    _validated_url(url, source)
    if source['require_sha256'] and (not isinstance(expected, str) or len(expected) != hashlib.sha256().digest_size * 2):
        raise ValueError('Every GRP/manifest download needs its declared SHA256')
    max_bytes = policy['max_manifest_bytes'] if manifest else policy['max_file_bytes']
    dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
    if expected and dest.is_file() and file_hash(dest) == expected: return
    partial = dest.with_suffix(dest.suffix + '.part')
    class NoUnapprovedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            _validated_url(newurl, source)
            return super().redirect_request(request, fp, code, message, headers, newurl)
    opener = urllib.request.build_opener(NoUnapprovedRedirect())
    error = None
    for attempt in range(policy['attempts']):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'WTAtlasOfflineBuilder'})
            digest = hashlib.sha256(); total = 0
            with opener.open(req, timeout=policy['timeout_seconds']) as response, partial.open('wb') as stream:
                while True:
                    chunk = response.read(policy['chunk_bytes'])
                    if not chunk: break
                    total += len(chunk)
                    if total > max_bytes: raise ValueError('Source exceeded configured maximum byte size')
                    digest.update(chunk); stream.write(chunk)
            if expected and digest.hexdigest() != expected: raise ValueError('Downloaded content SHA256 mismatch')
            partial.replace(dest)
            return
        except Exception as exc:
            error = exc
            partial.unlink(missing_ok=True)
            if attempt + 1 < policy['attempts']:
                time.sleep(policy['retry_backoff_seconds'] * (attempt + 1))
    raise RuntimeError('Source download failed: ' + str(error))


def acquire(root, config, source_path=None):
    store = JobStore(root, config)
    source = dict(config['source'])
    if source_path is not None: source.update(kind='local_directory', path=str(Path(source_path).resolve()))
    inputs = []
    if source['kind'] == 'local_directory':
        directory = resolved(root, source['path'])
        if not directory.is_dir(): raise ValueError('Configured client directory does not exist')
        files = directory.rglob('*.grp') if source['recursive'] else directory.glob('*.grp')
        inputs = [(path, None, {'kind': 'local_directory', 'name': path.name}) for path in sorted(files)]
    elif source['kind'] == 'manifest':
        if source.get('manifest_url'):
            manifest_path = store.work / 'manifests' / (source['manifest_sha256'] + '.json')
            _download(source['manifest_url'], manifest_path, source['manifest_sha256'], config, manifest=True)
        else:
            manifest_path = resolved(root, source['path'])
        manifest = read_json(manifest_path)
        for entry in manifest['files']:
            digest = entry['sha256']
            if not isinstance(digest, str) or len(digest) != hashlib.sha256().digest_size * 2 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('Invalid SHA256 in GRP manifest')
            raw = store.work / 'downloads' / (digest + '.grp')
            try:
                _download(entry['url'], raw, digest, config)
                inputs.append((raw, digest, {'kind': 'manifest', 'vehicle_id': entry.get('vehicle_id'), 'source_version': entry.get('source_version')}))
            except Exception as exc:
                store.update(digest, status='failed_acquisition', error=str(exc))
    else:
        raise ValueError('Unknown source kind; choose local_directory or manifest')
    if not inputs and not store.data['jobs']: raise ValueError('No GRP inputs found')
    for path, expected, origin in inputs:
        digest = file_hash(path, config['acquisition']['chunk_bytes'])
        if expected and digest != expected:
            store.update(expected, status='failed_hash', error='Local content no longer matches acquired hash')
            continue
        target = store.work / 'raw' / (digest + '.grp')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(path, target)
        if file_hash(target) != digest: raise ValueError('Content store hash mismatch')
        old = store.data['jobs'].get(digest)
        if old and old.get('status') in ['complete', 'decoded', 'waiting_profile']:
            continue
        store.update(digest, status='acquired', raw_path=str(target.relative_to(store.root)), origin=origin,
                     bytes=target.stat().st_size, error=None)
    return store.overview()


def profile_registry(root, config):
    directory = resolved(root, config['paths']['profiles'])
    result, issues, ambiguous = {}, [], set()
    for path in sorted(directory.glob('*.json')):
        try:
            profile = read_json(path)
            safe_id(profile['vehicle'])
            digest = profile['grp_sha256']
            if not isinstance(digest, str) or len(digest) != hashlib.sha256().digest_size * 2 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('Invalid profile GRP SHA256')
            if digest in ambiguous:
                raise ValueError('Ambiguous profiles for one GRP hash')
            if digest in result:
                result.pop(digest)
                ambiguous.add(digest)
                raise ValueError('Ambiguous profiles for one GRP hash; no duplicate was selected')
            if [material.get('node') for material in profile['materials']] != list(range(len(profile['materials']))):
                raise ValueError('Every collision node requires an ordered material entry')
            result[digest] = (path, profile)
        except Exception as exc:
            issues.append({'profile': path.name, 'status': 'rejected_profile', 'error': type(exc).__name__ + ': ' + str(exc)})
    return result, issues


def job_fingerprint(root, config, digest, profile_path, vendor, ammo_path):
    """One physical dependency identity shared by computation and publishing."""
    physics_data = profile_path.parent.parent if config['compute']['backend'] == 'cuda' and profile_path else vendor / 'data'
    physical_settings = {key: config['compute'].get(key) for key in ['backend', 'angles_deg', 'pixel_pitch_m', 'view', 'prefer_pinned_runtime_vertices', 'compiled_raster']}
    identity = {'grp': digest, 'profile': file_hash(profile_path) if profile_path else None,
                'config': physical_settings, 'numerics': config['numerics'], 'backend': config['compute']['backend'],
                'model_code': file_hash(Path(__file__).with_name('cpu_model.py')),
                'geometry_code': file_hash(Path(__file__).with_name('geometry.py')),
                'raster_code': file_hash(Path(__file__).with_name('raster.py')),
                'directory_code': file_hash(Path(__file__).parent / 'native_binding/grp_directory.py'),
                'decoder_code': file_hash(vendor / 'lib/grp_decoder.py'),
                'ammo': file_hash(ammo_path), 'view': file_hash(physics_data / 'view.json') if (physics_data / 'view.json').is_file() else None,
                'runtime_files': {p.name: file_hash(p) for p in sorted((physics_data / 'runtime').glob('*')) if p.is_file()},
                'vendor_code': {p.relative_to(vendor).as_posix(): file_hash(p) for p in sorted(vendor.rglob('*.py'))},
                'scope': config['scope']}
    return content_hash(identity)


def run_jobs(root, config, decode_only=False, vehicle_ids=None):
    store = JobStore(root, config); vendor = resolved(root, config['paths']['vendor'])
    registry, profile_issues = profile_registry(root, config)
    store.data['profile_issues'] = profile_issues
    store.save()
    write_json(store.work / 'profile-audit.json', {'issues': profile_issues, 'registered_profiles': len(registry)})
    backend = config['compute']['backend']
    ammo_path = resolved(root, config['compute'].get('ammunition_file', str(vendor / 'data/ammunition.json')))
    for digest in list(store.data['jobs']):
        job = store.data['jobs'][digest]
        path = resolved(root, job['raw_path']) if job.get('raw_path') else None
        if path is None: continue
        pair = registry.get(digest)
        profile_path, profile = pair if pair else (None, None)
        if vehicle_ids and (not profile or profile['vehicle'] not in vehicle_ids): continue
        target = profile['vehicle'] if profile else digest
        fingerprint = job_fingerprint(root, config, digest, profile_path, vendor, ammo_path)
        if not decode_only and not config['compute']['rebuild'] and job.get('physical_cache_key') == fingerprint and job.get('status') == 'complete':
            if job.get('results') and all((store.work / 'results' / fingerprint / result['file']).is_file() and file_hash(store.work / 'results' / fingerprint / result['file']) == result['sha256'] for result in job.get('results', [])):
                store.update(digest, status='complete', compute_cache_reused=True)
                continue
        prior_complete = job.get('status') == 'complete' and job.get('physical_cache_key') == fingerprint
        try:
            if file_hash(path) != digest: raise ValueError('Acquired GRP hash changed')
            store.update(digest, status='decoding', vehicle_id=profile['vehicle'] if profile else None, error=None)
            decoded = decode_grp(path, vendor, profile)
            mesh_report = export_mesh(decoded, store.work / 'decoded' / digest, path)
            mesh_resources = [{k: row[k] for k in ['resource_name', 'collision_nodes', 'triangles']} for row in mesh_report['resources']]
            store.update(digest, status='complete' if decode_only and prior_complete else 'decoded', mesh_resources=mesh_resources)
            if decode_only: continue
            if profile is None:
                store.update(digest, status='waiting_profile', error='Mesh decoded. Add matching SHA256 vehicle profile with complete DamageParts/material mapping before physics computation.')
                continue
            provenance = profile.get('provenance', {})
            if (provenance.get('client_version') != config['scope']['source_version']
                    or provenance.get('datamine_commit') != config['scope']['datamine_commit']):
                store.update(digest, status='waiting_version_alignment', error='Vehicle materials and declared ammunition/model source snapshot differ. Align the versioned inputs before computation.')
                continue
            store.update(digest, status='geometry', physical_cache_key=fingerprint, compute_cache_reused=False)
            out = store.work / 'results' / fingerprint
            if backend == 'cpu':
                meta, reused = build_cpu_layers(path, profile_path, vendor, store.work / 'geometry', config, decoded)
                store.update(digest, status='calculating', layer_metadata=str(meta.relative_to(store.root)), geometry_reused=reused)
                result = recalculate_layers(meta, ammo_path, out, config)
                store.update(digest, status='complete', results=result['results'], result_directory=str(out.relative_to(store.root)),
                             elapsed_seconds=result['elapsed_seconds'], physical_cache_key=fingerprint)
            elif backend == 'cuda':
                command = [sys.executable, str(vendor / 'grp_probability.py'), '--grp', str(path), '--data', str(profile_path.parent.parent),
                           '--ammo-json', str(ammo_path), '--angles', *map(str, config['compute']['angles_deg']),
                           '--pitch', str(config['compute']['pixel_pitch_m'] or read_json(vendor / 'data/view.json')['pixel_pitch_m']),
                           '--device', str(config['compute']['cuda_device']), '--ray-batch', str(config['compute']['cuda_ray_batch']),
                           '--out', str(out), '--cache-dir', str(store.work / 'geometry_cuda'), '--hd-scale', '0', '--no-images']
                if config['compute']['rebuild']: command.append('--rebuild')
                out.mkdir(parents=True, exist_ok=True)
                with (out / 'worker.log').open('w') as log:
                    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
                if result.returncode: raise RuntimeError('CUDA worker failed; inspect private work result worker.log')
                report = read_json(out / 'run.json')
                rows = [{'file': 'arrays/' + Path(group['array_file']).name, 'sha256': group['array_sha256'],
                         'target_id': target, 'ammo_id': group['key'].rsplit('.', 1)[-1]} for group in report['groups']]
                metadata = report['vehicles'][0]['layer_metadata']
                store.update(digest, status='complete', results=rows, layer_metadata=metadata,
                             result_directory=str(out.relative_to(store.root)), physical_cache_key=fingerprint)
            else:
                raise ValueError('compute.backend must be cpu or cuda')
        except Exception as exc:
            store.update(digest, status='failed', error=type(exc).__name__ + ': ' + str(exc))
    overview = store.overview()
    write_json(store.work / 'status.json', overview)
    return overview


def export_completed(root, config, output=None):
    """Convert completed jobs to the same public contract as imported research."""
    from .probability import export_atlas
    store = JobStore(root, config); vendor = resolved(root, config['paths']['vendor'])
    completed = [job for job in store.data['jobs'].values() if job.get('status') == 'complete']
    if not completed: raise ValueError('No completed jobs to export')
    registry, issues = profile_registry(root, config)
    ammo_path = resolved(root, config['compute'].get('ammunition_file', str(vendor / 'data/ammunition.json')))
    for job in completed:
        registered = registry.get(job['grp_sha256'])
        if registered is None or not job.get('results'):
            raise ValueError('Completed job lacks a unique current profile or result set; run the pipeline before export')
        expected = job_fingerprint(root, config, job['grp_sha256'], registered[0], vendor, ammo_path)
        if expected != job.get('physical_cache_key'):
            raise ValueError('Stale physical job cache; run the pipeline with current inputs before export')
    stage = store.work / 'computed_atlas'; stage.mkdir(parents=True, exist_ok=True)
    for part in ['arrays', 'geometry_layers', 'provenance']: (stage / part).mkdir(exist_ok=True)
    # A new stage must never silently retain removed jobs or stale ammo arrays.
    for old in (stage / 'arrays').glob('*.npz'): old.unlink()
    ammo_path = resolved(root, config['compute'].get('ammunition_file', str(vendor / 'data/ammunition.json')))
    ammo = read_json(ammo_path)
    if isinstance(ammo, dict): ammo = [ammo]
    summary = {'groups': [], 'algorithm': config['scope']['cpu_model_version'] if config['compute']['backend'] == 'cpu' else config['scope']['model_version'],
               'client_version': None, 'datamine_commit': None, 'created_at_utc': None}
    versions, commits = set(), set(); first_plan = None
    for job in completed:
        metadata = resolved(root, job['layer_metadata']); layer = read_json(metadata)
        plan = layer['plan']
        if first_plan is None: first_plan = plan
        if any(plan[k] != first_plan[k] for k in ['angles_deg', 'width', 'height', 'pixel_pitch_m']):
            raise ValueError('Mixed geometry plans must be exported into separate atlas snapshots')
        versions.add(layer.get('profile_provenance', {}).get('client_version'))
        commits.add(layer.get('profile_provenance', {}).get('datamine_commit'))
        group = {'vehicle': job['vehicle_id'], 'grp_sha256': job['grp_sha256'], 'layer_arrays_sha256': layer['arrays_sha256'], 'groups': []}
        shutil.copyfile(metadata, stage / 'geometry_layers' / metadata.name)
        for item in job['results']:
            file = resolved(root, job['result_directory']) / item['file']
            if file_hash(file) != item['sha256']: raise ValueError('Computed file hash changed before export')
            dest = stage / 'arrays' / file.name
            shutil.copyfile(file, dest)
            group['groups'].append({'array_file': 'arrays/' + dest.name, 'array_sha256': item['sha256']})
        summary['groups'].append(group)
    if len(versions) != 1 or len(commits) != 1:
        raise ValueError('Mixed source versions must be exported into separate atlas snapshots')
    summary['client_version'] = next(iter(versions)); summary['datamine_commit'] = next(iter(commits))
    write_json(stage / 'summary.json', summary); write_json(stage / 'provenance/plan.json', first_plan)
    write_json(stage / 'provenance/ammunition.json', ammo)
    write_json(stage / 'provenance/model_scope.json', {**config['scope'], 'computed_backend': config['compute']['backend'],
                                                     'source_version': summary['client_version'], 'datamine_commit': summary['datamine_commit'],
                                                     'cpu_geometry_not_native_runtime_parity': config['compute']['backend'] == 'cpu'})
    write_json(stage / 'format.json', {'per_ammunition_arrays': {'status': {'values': config['status_legend']}}})
    return export_atlas(stage, resolved(root, output or config['paths']['static_probability']), config)
