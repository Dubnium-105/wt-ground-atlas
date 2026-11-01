#!/usr/bin/env python3
"""Independently revalidate a completed offline kinetic missile research batch."""
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend')); sys.path.insert(0, str(ROOT / 'scripts'))
from wt_atlas.common import content_hash, file_hash, read_json, write_json
from wt_atlas.kinetic_research_audit import summarize_checks
from wt_atlas.kinetic_research_batch import plan_targets
from rebuild_kinetic_research_batch import MARKER, verified_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repository', 'catalog', 'ammunition', 'source-report', 'config', 'bindings', 'batch', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(); started = time.perf_counter()
    inputs = {name: getattr(args, name.replace('-', '_')) for name in ('catalog', 'ammunition', 'source-report', 'config', 'bindings')}
    if args.out.resolve().is_relative_to(args.batch.resolve()) or args.out.resolve() in {p.resolve() for p in inputs.values()}:
        raise ValueError('Keep audit output outside batch and source inputs')
    marker = read_json(args.batch / MARKER); status = read_json(args.batch / 'progress.json')
    hashes = {name: file_hash(path) for name, path in inputs.items()}
    if hashes != marker['input_sha256'] or status.get('status') != 'completed' or status.get('publishable') is not False:
        raise ValueError('Batch source identity or completion status is invalid')
    code_hashes = marker['code_sha256']; vendor = ROOT / 'backend/vendor/grp_probability_v2'
    for name, expected in code_hashes.items():
        if name == 'vendor_code':
            actual = content_hash({p.relative_to(vendor).as_posix(): file_hash(p) for p in sorted(vendor.rglob('*.py'))})
        elif name == 'vendor_manifest': actual = file_hash(vendor / 'MANIFEST.json')
        else: actual = file_hash(ROOT / name)
        if actual != expected: raise ValueError('Batch source code changed: ' + name)
    catalog, profiles, source, config, binding_report = (read_json(inputs[name]) for name in
        ('catalog', 'ammunition', 'source-report', 'config', 'bindings'))
    if (source.get('output_sha256') != hashes['ammunition'] or source.get('publishable') is not False or
        source.get('rejected') or source['input_sha256']['catalog'] != hashes['catalog'] or
        binding_report.get('status') != 'completed' or binding_report.get('errors')):
        raise ValueError('Source provenance or geometry receipt invalid')
    plan = plan_targets(catalog, config, binding_report['bindings'], profiles)
    if content_hash(plan) != marker['plan_sha256'] or read_json(args.batch / 'plan.json') != plan:
        raise ValueError('Batch plan differs from independently reconstructed plan')
    if (status.get('verified_targets'), status.get('verified_maps')) != (plan['target_count'], plan['planned_maps']):
        raise ValueError('Batch completion counts do not match the plan')
    verifier = code_hashes['scripts/verify_kinetic_missile_points.py']
    summaries = []; total_bytes = 0
    for target in plan['targets']:
        if not verified_target(args.batch, args.repository, target, profiles, hashes['source-report'], verifier):
            raise ValueError('Independent geometry or array verification failed: ' + target['vehicle_id'])
        report = read_json(args.batch / 'checks' / (target['vehicle_id'] + '.json'))
        summary = summarize_checks(report)
        maps = args.batch / 'maps' / target['vehicle_id'] / 'arrays'
        total_bytes += sum((maps / (row['research_map']['map_id'] + '.npz')).stat().st_size for row in report['checks'])
        summaries.append({'vehicle_id': target['vehicle_id'], **summary})
        if len(summaries) % 20 == 0: print(f'{len(summaries)}/{plan["target_count"]} targets independently verified', flush=True)
    if any(file_hash(path) != hashes[name] for name, path in inputs.items()): raise ValueError('Audit input changed')
    result = {'schema_version': '1.0.0', 'status': 'passed', 'source_version': plan['source_version'],
              'datamine_commit': plan['datamine_commit'], 'owner_id': plan['owner_id'], 'ammo_id': plan['ammo_id'],
              'input_sha256': hashes, 'plan_sha256': marker['plan_sha256'], 'target_count': len(summaries),
              'verified_arrays': len(summaries) * 4, 'angles_per_map': plan['angles_per_map'],
              'compressed_array_bytes': total_bytes, 'sampled_rays': sum(s['sampled_rays'] for s in summaries),
              'checks_with_partial_probability': sum(s['checks_with_partial_probability'] for s in summaries),
              'max_probability_delta': max(s['max_probability_delta'] for s in summaries),
              'duration_seconds': time.perf_counter() - started, 'source_probability_maps_read': 0,
              'game_probability_validated': False, 'reference_only': True, 'publishable': False, 'formal_rankings': 0,
              'scope': 'Independent source/geometry/array revalidation of conditional impact research only; base BR, not dynamic eligibility. No flight, activation, hit or damage claim.',
              'code_sha256': {'audit': file_hash(Path(__file__)), 'summary': file_hash(ROOT / 'backend/wt_atlas/kinetic_research_audit.py')},
              'targets': summaries}
    write_json(args.out, result)
    print(f"PASS: {result['target_count']} targets, {result['verified_arrays']} arrays, {result['sampled_rays']} CPU/CUDA sampled rays", flush=True)


if __name__ == '__main__': main()
