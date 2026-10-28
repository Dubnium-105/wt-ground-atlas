"""Build every catalog ground configuration from a pinned installed client.

Resource names come from the unit's model field, never fuzzy vehicle names.
Shared GRPs remain distinct material/configuration jobs. Client files are read
only; extracted data, source copies and resumable jobs live under data/work.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import json
import gzip
import math
import shutil
import struct
import subprocess
import time
import numpy as np
from .common import read_json, write_json, file_hash, content_hash, resolved, atomic_write, stage_verified_file
from .geometry import decode_grp, build_cpu_layers
from .jobs import job_fingerprint, utc_now
from .storage import recalculate_layers_atomic as recalculate_layers
from .native_binding.grp_directory import parse_grp_directory
from .native_binding.ordered_blk import parse_ordered_blk
from .native_binding.native_templates import expand_part_defaults
from .native_binding.native_material import register_mechanisms, typed_parameter
from .native_binding.native_class import default_class_block, load_class_block, decoded_class_flags
from .native_binding.native_part_binding import filtered_damage_parts, bind_pristine_nodes


def progress(**fields):
    print(json.dumps(fields, ensure_ascii=False), flush=True)


def client_paths(root, config):
    work = resolved(root, config.get('client', {}).get('work', 'data/work/client'))
    return work, work / 'extracted/aces.vromfs.bin_u'


def extract_client(root, config, source):
    work, extracted = client_paths(root, config)
    evidence = read_json(Path(__file__).parent / 'native_binding/provenance.json')
    pack = Path(source) / 'aces.vromfs.bin'
    pack_hash = file_hash(pack)
    if pack_hash != evidence['aces_vromfs_sha256']:
        raise ValueError('Client configuration pack differs from the version validated by the material loaders: ' + pack_hash)
    if (config['scope']['source_version'] != evidence['client_version']
            or config['scope']['datamine_commit'] != evidence['datamine_commit']):
        raise ValueError('Configured physics source does not match the installed material loader snapshot')
    tool = resolved(root, config['client']['extractor'])
    if file_hash(tool) != evidence['extractor_sha256']:
        raise ValueError('Client extraction tool hash differs from its provenance')
    folders = ['gamedata/units/tankmodels', 'gamedata/damage_model',
               'gamedata/weapons', 'config/damagemodelparts.blk']
    marker = work / 'extraction.json'
    previous = read_json(marker) if marker.exists() else {}
    if previous.get('vromfs_sha256') == pack_hash and previous.get('folders') == folders and all((extracted / p).exists() for p in folders):
        return extracted, evidence
    work.mkdir(parents=True, exist_ok=True)
    for folder in folders:
        log = work / ('extract-' + folder.replace('/', '_') + '.log')
        with log.open('w', encoding='utf-8') as stream:
            subprocess.run([str(tool), 'unpack_vromf', '-i', str(pack), '-o', str(work / 'extracted'),
                            '--folder', folder, '--format', 'BlkText', '--blk_extension', 'blk'],
                           stdout=stream, stderr=subprocess.STDOUT, check=True)
    write_json(marker, {'vromfs_sha256': pack_hash, 'client_version': evidence['client_version'],
                        'extractor_sha256': evidence['extractor_sha256'], 'folders': folders})
    return extracted, evidence


def scan_resources(source):
    """Only installed content packs; download caches/UserMods are not active inputs."""
    resources = defaultdict(list); failures = []; count = 0
    source = Path(source)
    packs = {p.relative_to(source/'content').as_posix(): p for p in (source/'content').rglob('*.grp')}
    patches = {p.relative_to(source/'patch/content').as_posix(): p for p in (source/'patch/content').rglob('*.grp')}
    replaced = len(set(packs) & set(patches))
    patch_resources = defaultdict(list)
    for path in [*sorted(packs.values()), *sorted(patches.values())]:
        count += 1
        try:
            with path.open('rb') as stream:
                head = stream.read(16)
                size = struct.unpack_from('<I', head, 8)[0] + 16
                if size > path.stat().st_size:
                    raise ValueError('GRP descriptor exceeds file size')
                stream.seek(0); directory = parse_grp_directory(stream.read(size))
            for entry in directory['entries']:
                if entry['class_id'] == 0xACE50000:
                    target = patch_resources if path.is_relative_to(source/'patch/content') else resources
                    target[entry['name']].append(str(path))
        except Exception as exc:
            failures.append({'path': str(path.relative_to(source)), 'error': str(exc)})
    resource_overrides = len(set(resources) & set(patch_resources))
    resources.update(patch_resources)
    return resources, {'active_grp_files': count, 'patch_grp_files': len(patches), 'patch_overrides': replaced,
                       'patched_collision_resources': len(patch_resources), 'collision_resource_overrides': resource_overrides,
                       'collision_resource_names': len(resources), 'directory_failures': failures}


_BINDING = None


def binding_context(extracted):
    global _BINDING
    if _BINDING is not None and _BINDING['extracted'] == str(extracted):
        return _BINDING
    extracted = Path(extracted)
    classes_file = extracted / 'gamedata/damage_model/armor_classes.blk'
    defaults_file = extracted / 'config/damagemodelparts.blk'
    mechanisms_file = extracted / 'gamedata/damage_model/damage_types.blk'
    classes = parse_ordered_blk(classes_file)['children']
    expanded = expand_part_defaults(parse_ordered_blk(defaults_file))
    mechanism = register_mechanisms(parse_ordered_blk(mechanisms_file))
    _BINDING = {'extracted': str(extracted), 'classes': classes, 'class_map': {b['name']: b for b in classes},
                'defaults': {b['name']: b['params'] for b in expanded['parts']},
                'template_ids': {b['name']: i for i, b in enumerate(expanded['parts'])},
                'mechanisms': mechanism, 'source_hashes': {p.name: file_hash(p) for p in [classes_file, defaults_file, mechanisms_file]}}
    return _BINDING


def make_materials(damage, nodes, context):
    if typed_parameter(damage['params'], 'formatVersion', 'i') != 2:
        raise ValueError('Unsupported DamageParts format')
    blocks = {}
    def walk(block, parent=''):
        block['path'] = (parent + '/' if parent else '') + block['name']
        if block['path'] in blocks:
            raise ValueError('Duplicate DamageParts block path: ' + block['path'])
        blocks[block['path']] = block
        for child in block['children']:
            walk(child, block['path'])
    walk(damage)
    names = [node['name'] for node in nodes]
    name_ids = {name: i for i, name in enumerate(dict.fromkeys([*context['class_map'], *names]))}
    parts = filtered_damage_parts(damage, context['classes'], names, name_ids)
    loaded = {}; sources = {}; unresolved = {}
    descriptor = context['mechanisms']
    for part in parts:
        state = default_class_block(); overlays = []; armor_classes = []; issues = []
        prefix = context['defaults'].get(part['name'])
        if prefix is not None:
            state = load_class_block(prefix, descriptor['names'], descriptor['parents'], state)
            overlays.append('@expanded_defaults/' + part['name'])
        for callback in part['callbacks']:
            name = callback['block']
            block = blocks.get(name, context['class_map'].get(name))
            if block is None:
                raise ValueError('Unresolved material overlay: ' + name)
            if callback['alternate_block']:
                issues.append('alternate_material:' + callback['alternate_block'])
            referenced = typed_parameter(block['params'], 'armorClass', 't')
            if referenced and referenced not in context['class_map']:
                issues.append('missing_armor_class:' + referenced)
            state = load_class_block(block['params'], descriptor['names'], descriptor['parents'], state)
            overlays.append(name)
            if name in context['class_map']:
                armor_classes.append(name)
        loaded[part['name']] = state
        sources[part['name']] = {'source_path': part['path'], 'source_armor_classes': armor_classes, 'source_overlay_blocks': overlays}
        if issues:
            unresolved[part['name']] = issues
        part['flags'] = state['flags']
    binding = bind_pristine_nodes(parts, names, name_ids, context['template_ids'])
    materials = []
    for node, triplet in zip(nodes, binding['bindings']):
        part_id = triplet[0]; name = binding['part_names'][part_id] if part_id >= 0 else None
        state = loaded.get(name)
        flags = binding['class_flags'][part_id] if part_id >= 0 else None
        if name in unresolved:
            state = None; flags = None
        materials.append({'node': node['index'], 'part_id': part_id, 'name': name,
                          'behavior_flags': node['behavior_flags'], 'class_flags': flags, 'loaded_class': state,
                          'decoded_flags': decoded_class_flags(flags) if state is not None else None,
                          **sources.get(name, {'source_path': None, 'source_armor_classes': [], 'source_overlay_blocks': []}),
                          'unresolved': unresolved.get(name, [])})
    return materials, unresolved


def build_profile(task):
    root, config, extracted, evidence, vehicle, raw, collision = task
    root = Path(root); work, _ = client_paths(root, config)
    target = vehicle['id']; output = work / 'profiles' / (target + '.json')
    unit_path = Path(extracted) / 'gamedata/units/tankmodels' / (target + '.blk')
    vendor = resolved(root, config['paths']['vendor']); context = binding_context(extracted)
    unit = parse_ordered_blk(unit_path)
    damage = next(b for b in unit['children'] if b['name'] == 'DamageParts')
    decoded = decode_grp(raw, vendor, resource_name=collision)[0]
    parsed = decoded['parsed']; materials, unresolved = make_materials(damage, parsed['nodes'], context)
    vertices = np.concatenate([np.asarray(c['verts']) for c in parsed['chunks'] if len(c['verts'])])
    minimum, maximum = vertices.min(axis=0), vertices.max(axis=0)
    center = (minimum + maximum) / 2; half = (maximum - minimum) / 2
    pitch = config['compute']['pixel_pitch_m'] or read_json(vendor / 'data/view.json')['pixel_pitch_m']
    # Bounding radius includes oblique views and each vehicle's full authored hull.
    margin = config['client']['view_margin_m']; width = math.ceil((2*math.hypot(half[0], half[2])+2*margin)/pitch)*pitch
    profile = {'schema_version': 1, 'vehicle': target, 'display_name': vehicle['name'], 'grp_sha256': file_hash(raw),
               'collision_resource': collision,
               'collision_nodes': [{k: n[k] for k in ['index','name','indices_count','behavior_flags']} for n in parsed['nodes']],
               'view_group': {'vehicle': target, 'center_xz_m': center[[0,2]].tolist(), 'half_xz_m': half[[0,2]].tolist()},
               'view_plan': {'world_width_m': width, 'top_y_m': float(maximum[1]+margin),
                             'bottom_y_m': float(minimum[1]-margin), 'ray_limit_m': float(2*math.hypot(half[0],half[2])+2*margin+1),
                             'common_metric_scale_all_vehicles_angles': True},
               'materials': materials,
               'provenance': {'client_version': evidence['client_version'], 'datamine_commit': evidence['datamine_commit'],
                              'aces_vromfs_sha256': evidence['aces_vromfs_sha256'], 'unit_sha256': file_hash(unit_path),
                              'class_source_sha256': context['source_hashes']['armor_classes.blk'],
                              'source_hashes': context['source_hashes'], 'binding_method': 'ordered_pristine_format2',
                              'binding_port_sha256': content_hash({p.name: file_hash(p) for p in (Path(__file__).parent/'native_binding').glob('*.py')}),
                              'configuration_state': 'pristine_authored_pose', 'unresolved_materials': unresolved}}
    write_json(output, profile)
    return {'vehicle_id': target, 'grp_sha256': profile['grp_sha256'], 'raw_path': str(Path(raw).relative_to(root)),
            'profile_path': str(output.relative_to(root)), 'collision_resource': collision, 'nodes': len(materials),
            'triangles': sum(len(c['faces']) for c in parsed['chunks']), 'unresolved_materials': len(unresolved),
            'status': 'prepared', 'profile_sha256': file_hash(output)}


def prepare_client(root, config, source, workers=1, vehicle_ids=None):
    root = Path(root); source = Path(source).resolve(); work, _ = client_paths(root, config)
    extracted, evidence = extract_client(root, config, source)
    resources, inventory = scan_resources(source)
    catalog = read_json(root / 'data/snapshots/catalog.json')
    vehicles = [v for v in catalog['vehicles'] if v['kind']=='ground' and (not vehicle_ids or v['id'] in vehicle_ids)]
    tasks = []; rows = []; raw_cache = {}
    progress(stage='inventory', vehicles=len(vehicles), **{k:v for k,v in inventory.items() if k!='directory_failures'})
    for vehicle in vehicles:
        target = vehicle['id']; unit = extracted / 'gamedata/units/tankmodels' / (target + '.blk')
        try:
            model = typed_parameter(parse_ordered_blk(unit)['params'], 'model', 't')
            collision = model + '_collision'; hits = resources.get(collision, [])
            if len(hits) != 1:
                rows.append({'vehicle_id': target, 'model': model, 'collision_resource': collision,
                             'status': 'missing_resource' if not hits else 'ambiguous_resource', 'candidates': hits})
                continue
            path = hits[0]
            if path not in raw_cache:
                digest = file_hash(path); dest = work / 'raw' / (digest + '.grp'); dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists() or file_hash(dest) != digest:
                    shutil.copyfile(path, dest)
                raw_cache[path] = dest
            tasks.append((str(root), config, str(extracted), evidence, vehicle, str(raw_cache[path]), collision))
        except Exception as exc:
            rows.append({'vehicle_id': target, 'status': 'failed_prepare', 'error': str(exc)})
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(build_profile, task): task[4]['id'] for task in tasks}
        for n, future in enumerate(as_completed(pending), 1):
            target = pending[future]
            try:
                row = future.result()
                old_file = work / 'jobs' / (target + '.json')
                if old_file.exists():
                    old = read_json(old_file)
                    if old.get('profile_sha256') == row['profile_sha256'] and old.get('status')=='complete':
                        row = old
            except Exception as exc:
                row = {'vehicle_id': target, 'status': 'failed_prepare', 'error': type(exc).__name__ + ': ' + str(exc)}
            rows.append(row); write_json(work / 'jobs' / (target + '.json'), row)
            if n % 25 == 0 or n == len(tasks):
                progress(stage='profiles', done=n, total=len(tasks), failures=sum(r['status']=='failed_prepare' for r in rows))
    # Persist missing targets as well, so subsequent runs cannot reuse obsolete results.
    for row in rows:
        if row['status'] in ['missing_resource','ambiguous_resource','failed_prepare']:
            write_json(work/'jobs'/(row['vehicle_id']+'.json'), row)
    report = {'schema_version': 1, 'client_version': evidence['client_version'],
              'source_vromfs_sha256': evidence['aces_vromfs_sha256'], 'catalog_ground_vehicles': len(vehicles),
              **inventory, 'unique_acquired_grps': len(raw_cache), 'status_counts': dict(Counter(r['status'] for r in rows)),
              'collision_nodes': sum(r.get('nodes',0) for r in rows), 'unresolved_materials': sum(r.get('unresolved_materials',0) for r in rows),
              'items': sorted(rows, key=lambda r:r['vehicle_id'])}
    write_json(work/'inventory.json', report); write_json(root/'reports/client-grp-coverage.json', report)
    return {k:v for k,v in report.items() if k not in ['items','directory_failures']}


def compute_vehicle(task):
    root, config, row = task; root = Path(root); work, _ = client_paths(root, config)
    target = row['vehicle_id']; job_path = work/'jobs'/(target+'.json')
    vendor = resolved(root, config['paths']['vendor']); profile = resolved(root, row['profile_path'])
    ammo = resolved(root, config['compute'].get('ammunition_file', str(vendor/'data/ammunition.json')))
    fingerprint = job_fingerprint(root, config, row['grp_sha256'], profile, vendor, ammo)
    if row.get('physical_cache_key')==fingerprint and row.get('status')=='complete':
        if all((root/row['result_directory']/r['file']).is_file() and file_hash(root/row['result_directory']/r['file'])==r['sha256'] for r in row.get('results',[])) and row.get('results'):
            return {**row, 'compute_cache_reused': True}
    started = time.perf_counter()
    try:
        provenance = read_json(profile)['provenance']
        if (provenance['client_version'] != config['scope']['source_version']
                or provenance['datamine_commit'] != config['scope']['datamine_commit']):
            raise ValueError('Vehicle material and ammunition/model source snapshots differ')
        row.update(status='geometry', physical_cache_key=fingerprint, started_at=utc_now(), compute_cache_reused=False)
        write_json(job_path, row)
        raw = resolved(root, row['raw_path'])
        metadata, reused = build_cpu_layers(raw, profile, vendor, work/'geometry', config)
        row.update(status='calculating', layer_metadata=str(metadata.relative_to(root)), geometry_reused=reused)
        write_json(job_path, row)
        dest = work/'results'/fingerprint
        result = recalculate_layers(metadata, ammo, dest, config)
        row.update(status='complete', results=result['results'], result_directory=str(dest.relative_to(root)),
                   elapsed_seconds=time.perf_counter()-started, finished_at=utc_now(), error=None)
    except Exception as exc:
        row.update(status='failed_compute', error=type(exc).__name__+': '+str(exc))
    write_json(job_path, row)
    return row


def client_status(root, config):
    work, _ = client_paths(root, config)
    rows = [read_json(p) for p in sorted((work/'jobs').glob('*.json'))]
    return {'jobs': len(rows), 'status_counts': dict(Counter(r['status'] for r in rows)),
            'results': sum(len(r.get('results',[])) for r in rows if r['status']=='complete'),
            'issues': [{k:r.get(k) for k in ['vehicle_id','status','error','collision_resource']} for r in rows if r['status'].startswith('failed') or r['status'] in ['missing_resource','ambiguous_resource']]}


def compute_client(root, config, workers=1, vehicle_ids=None):
    work, _ = client_paths(root, config)
    rows = [read_json(p) for p in sorted((work/'jobs').glob('*.json'))]
    rows = [r for r in rows if r.get('profile_path') and (not vehicle_ids or r['vehicle_id'] in vehicle_ids)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(compute_vehicle, (str(root),config,row)):row['vehicle_id'] for row in rows}
        for n, future in enumerate(as_completed(pending),1):
            row = future.result()
            progress(stage='compute', done=n, total=len(rows), vehicle=row['vehicle_id'], status=row['status'],
                     seconds=round(row.get('elapsed_seconds',0),2), reused=row.get('compute_cache_reused',False))
    report = client_status(root, config)
    write_json(Path(root)/'reports/client-compute.json', report)
    return report


def publish_client(root, config):
    from .probability import export_atlas
    root=Path(root); work,_=client_paths(root,config); vendor=resolved(root,config['paths']['vendor'])
    rows=[read_json(p) for p in sorted((work/'jobs').glob('*.json'))]
    completed=[r for r in rows if r['status']=='complete']
    if not completed:
        raise ValueError('No completed client jobs to publish')
    ammo=resolved(root,config['compute'].get('ammunition_file',str(vendor/'data/ammunition.json')))
    stage=work/'atlas'; groups=[]; first_plan=None
    for sub in ['arrays','geometry_layers','provenance']:
        (stage/sub).mkdir(parents=True,exist_ok=True)
    expected_names=set()
    for row in completed:
        expected=job_fingerprint(root,config,row['grp_sha256'],root/row['profile_path'],vendor,ammo)
        if row['physical_cache_key']!=expected:
            raise ValueError('Stale client computation: '+row['vehicle_id'])
        layer=root/row['layer_metadata']; meta=read_json(layer)
        first_plan=first_plan or meta['plan']
        shutil.copyfile(layer,stage/'geometry_layers'/layer.name)
        group={'vehicle':row['vehicle_id'],'grp_sha256':row['grp_sha256'],'layer_arrays_sha256':meta['arrays_sha256'],'groups':[]}
        for item in row['results']:
            source=root/row['result_directory']/item['file']
            dest=stage/'arrays'/source.name; expected_names.add(dest.name)
            stage_verified_file(source,dest,item['sha256'])
            group['groups'].append({'array_file':'arrays/'+dest.name,'array_sha256':item['sha256']})
        groups.append(group)
    for old in (stage/'arrays').glob('*.npz'):
        if old.name not in expected_names:
            old.unlink()
    write_json(stage/'summary.json',{'groups':groups,'algorithm':config['scope']['cpu_model_version'],
        'client_version':config['scope']['source_version'],'datamine_commit':config['scope']['datamine_commit'],'created_at_utc':utc_now()})
    write_json(stage/'provenance/plan.json',first_plan); write_json(stage/'provenance/ammunition.json',read_json(ammo))
    write_json(stage/'provenance/model_scope.json',{**config['scope'],'computed_backend':'cpu','configuration_state':'pristine_authored_pose'})
    write_json(stage/'format.json',{'per_ammunition_arrays':{'status':{'values':config['status_legend']}}})
    output=resolved(root,config['paths']['static_probability'])
    arrays=sorted((stage/'arrays').glob('*.npz'))
    workers=min(config['client']['workers'],len(arrays))
    started=time.perf_counter();reports=[];indexes=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(export_atlas,stage,output,config,arrays[i::workers],work/'export-parts'/str(i)):i for i in range(workers)}
        for future in as_completed(pending):
            i=pending[future];reports.append(future.result())
            indexes.append(read_json(work/'export-parts'/str(i)/'index.json'))
            progress(stage='export', batches_done=len(reports), batches_total=workers)
    index=indexes[0];index.pop('content_hash',None)
    index['entries']=sorted([entry for part in indexes for entry in part['entries']],key=lambda entry:entry['key'])
    index['content_hash']=content_hash(index)
    write_json(output/'index.json',index)
    atomic_write(output/'index.json.gz',gzip.compress((output/'index.json').read_bytes(),compresslevel=config['export']['gzip_level'],mtime=0))
    report=dict(reports[0])
    for key in ['input_groups','views','valid_samples','uncompressed_shard_bytes','gzip_shard_bytes','reused_shards','reused_gzip_shards']:
        report[key]=sum(r[key] for r in reports)
    report.update(targets=len(completed),quantization_max_abs_error=max(r['quantization_max_abs_error'] for r in reports),
                  quantization_mean_abs_error=sum((r['quantization_mean_abs_error'] or 0)*r['valid_samples'] for r in reports)/report['valid_samples'] if report['valid_samples'] else None,
                  index_bytes=(output/'index.json').stat().st_size,index_gzip_bytes=(output/'index.json.gz').stat().st_size,
                  content_hash=index['content_hash'],build_seconds=time.perf_counter()-started,physical_recalculation_performed=True)
    write_json(output/'export-report.json',report)
    report['client_jobs']=client_status(root,config)
    write_json(root/'reports/client-probability-export.json',report)
    return report
