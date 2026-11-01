#!/usr/bin/env python3
"""Resume isolated missile research maps across verified base-BR targets."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import content_hash, file_hash, read_json, write_json
from wt_atlas.forecast.data import safe_file
from wt_atlas.forecast.inventory import verify_array
from wt_atlas.kinetic_research_batch import plan_targets
from wt_atlas.research_maps import MARKER as MAP_MARKER

MARKER = '.wt-kinetic-research-batch'


def verified_target(root, repository, target, profiles, source_report_sha, verifier_sha):
    """Recheck every retained array and exact input identity before resuming."""
    report_path = root / 'checks' / (target['vehicle_id'] + '.json')
    maps = root / 'maps' / target['vehicle_id']
    try:
        report = read_json(report_path); marker = read_json(maps / MAP_MARKER)
        if (report.get('status') != 'passed' or report.get('publishable') is not False or
            report.get('target') != target['vehicle_id'] or report.get('source_report_sha256') != source_report_sha or
            report.get('verification_code_sha256') != verifier_sha or marker.get('target_sha256') != target['target_sha256'] or
            marker.get('source_report_sha256') != source_report_sha or marker.get('verifier_sha256') != verifier_sha):
            return False
        job_path = safe_file(repository / 'data/work/client/jobs', target['vehicle_id'] + '.json')
        if file_hash(job_path) != target['source_job_sha256']: return False
        job = read_json(job_path); layer = safe_file(repository, job['layer_metadata'].replace('\\', '/'))
        if file_hash(layer) != marker.get('layer_metadata_sha256') or file_hash(layer.with_suffix('.npz')) != marker.get('layer_arrays_sha256'):
            return False
        expected = {p['distance_m']: p for p in profiles}; rows = report.get('checks', [])
        if len(rows) != len(expected) or {r['distance_m'] for r in rows} != set(expected): return False
        for row in rows:
            result = row['research_map']; map_id = result['map_id']
            if not isinstance(map_id, str) or not re.fullmatch(r'[0-9a-f]{64}', map_id): return False
            meta = read_json(maps / 'arrays' / (map_id + '.json')); profile = expected[row['distance_m']]
            if (result.get('reference_only') is not True or meta.get('reference_only') is not True or
                meta.get('publishable') is not False or meta.get('target_id') != target['vehicle_id'] or
                meta.get('target_sha256') != target['target_sha256'] or meta.get('ammo_id') != profile['name'] or
                meta.get('ammo_profile_sha256') != content_hash(profile) or
                meta.get('physical_distance_m') != profile['distance_m'] or
                meta.get('source_version') != profile['provenance']['client_version'] or
                meta.get('datamine_commit') != profile['provenance']['datamine_commit'] or
                meta.get('array_sha256') != result['array_sha256']): return False
            verified = verify_array((str(maps), meta, 512 * 1024**2))
            if verified['status'] != 'verified' or verified['angles'] != target['angles_deg']: return False
        return True
    except (OSError, KeyError, ValueError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repository', 'catalog', 'ammunition', 'source-report', 'config', 'bindings', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--limit', type=int, help='Stop after this many targets, preserving verified checkpoints')
    args = parser.parse_args(); started = time.perf_counter()
    if args.limit is not None and args.limit < 1: raise ValueError('Positive limit required')
    output = args.output.resolve()
    for protected in (ROOT, args.repository.resolve()):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError('Keep batch output outside source repositories')
    if any(p.is_symlink() for p in (args.output, *args.output.parents)):
        raise ValueError('Symlink batch output')
    inputs = {name: getattr(args, name.replace('-', '_')) for name in ('catalog', 'ammunition', 'source-report', 'config', 'bindings')}
    input_hashes = {name: file_hash(path) for name, path in inputs.items()}
    catalog, profiles, source, config, binding_report = (read_json(inputs[name]) for name in
        ('catalog', 'ammunition', 'source-report', 'config', 'bindings'))
    if (source.get('output_sha256') != input_hashes['ammunition'] or source.get('publishable') is not False or
        source.get('rejected') or source['input_sha256']['catalog'] != input_hashes['catalog'] or
        binding_report.get('status') != 'completed' or binding_report.get('errors')):
        raise ValueError('Source report or independent bindings are not verified')
    plan = plan_targets(catalog, config, binding_report['bindings'], profiles)
    code_paths = [Path(__file__), ROOT / 'backend/wt_atlas/kinetic_research_batch.py',
                  ROOT / 'scripts/verify_kinetic_missile_points.py', ROOT / 'scripts/prepare_kinetic_missile_points.py',
                  ROOT / 'backend/wt_atlas/kinetic_missile_points.py', ROOT / 'backend/wt_atlas/research_maps.py',
                  ROOT / 'backend/wt_atlas/cpu_model.py', ROOT / 'backend/wt_atlas/cuda_range.py',
                  ROOT / 'backend/wt_atlas/forecast/inventory.py', ROOT / 'backend/wt_atlas/forecast/data.py',
                  ROOT / 'backend/wt_atlas/npz_io.py', ROOT / 'backend/wt_atlas/common.py', ROOT / 'config/pipeline.json']
    code_hashes = {p.relative_to(ROOT).as_posix(): file_hash(p) for p in code_paths}
    vendor = ROOT / 'backend/vendor/grp_probability_v2'
    code_hashes['vendor_code'] = content_hash({p.relative_to(vendor).as_posix(): file_hash(p) for p in sorted(vendor.rglob('*.py'))})
    code_hashes['vendor_manifest'] = file_hash(vendor / 'MANIFEST.json')
    identity = {'schema_version': '1.0.0', 'input_sha256': input_hashes, 'code_sha256': code_hashes,
                'plan_sha256': content_hash(plan), 'reference_only': True, 'publishable': False}
    marker = output / MARKER
    if marker.is_symlink() or (output / 'checks').is_symlink() or (output / 'maps').is_symlink():
        raise ValueError('Symlink batch output member')
    if output.exists() and any(output.iterdir()) and not marker.is_file(): raise ValueError('Unmanaged batch output')
    if marker.exists() and read_json(marker) != identity: raise ValueError('Batch input or model changed')
    output.mkdir(parents=True, exist_ok=True)
    if not marker.exists(): write_json(marker, identity)
    (output / 'checks').mkdir(exist_ok=True); (output / 'maps').mkdir(exist_ok=True)
    write_json(output / 'plan.json', plan)
    state = {'status': 'running', 'planned_targets': plan['target_count'], 'planned_maps': plan['planned_maps'],
             'verified_targets': 0, 'verified_maps': 0, 'resumed_targets': 0, 'new_targets': 0,
             'formal_rankings': 0, 'publishable': False, 'reference_only': True}
    verifier = code_hashes['scripts/verify_kinetic_missile_points.py']
    def progress():
        state['duration_seconds'] = time.perf_counter() - started
        write_json(output / 'progress.json', state)
        print(f"{state['verified_targets']}/{state['planned_targets']} targets; {state['verified_maps']} maps", flush=True)
    progress()
    for target in plan['targets']:
        if args.limit is not None and state['verified_targets'] >= args.limit: break
        if any(file_hash(path) != input_hashes[name] for name, path in inputs.items()):
            raise ValueError('Batch input changed')
        if verified_target(output, args.repository, target, profiles, input_hashes['source-report'], verifier):
            state['resumed_targets'] += 1
        else:
            if any(file_hash(path) != code_hashes[path.relative_to(ROOT).as_posix()] for path in code_paths):
                raise ValueError('Batch code or configuration changed')
            command = [sys.executable, str(ROOT / 'scripts/verify_kinetic_missile_points.py'),
                '--repository', str(args.repository), '--catalog', str(args.catalog), '--ammunition', str(args.ammunition),
                '--source-report', str(args.source_report), '--target', target['vehicle_id'],
                '--maps-dir', str(output / 'maps' / target['vehicle_id']),
                '--out', str(output / 'checks' / (target['vehicle_id'] + '.json'))]
            child = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            if child.returncode or not verified_target(output, args.repository, target, profiles, input_hashes['source-report'], verifier):
                state.update(status='failed', failed_target=target['vehicle_id'],
                             error=(child.stderr or child.stdout or 'Target output verification failed')[-3000:])
                progress(); raise SystemExit(1)
            state['new_targets'] += 1
        state['verified_targets'] += 1; state['verified_maps'] += len(profiles); progress()
    state['status'] = 'completed' if state['verified_targets'] == plan['target_count'] else 'partial'
    progress()


if __name__ == '__main__': main()
