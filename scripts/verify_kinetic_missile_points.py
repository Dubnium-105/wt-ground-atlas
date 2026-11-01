#!/usr/bin/env python3
"""Cross-check exact missile impact points on real ordered geometry, CPU vs V2 CUDA."""
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import content_hash, file_hash, read_json, write_json
from wt_atlas.cpu_model import PreparedLayers
from wt_atlas.cuda_range import prepare
from wt_atlas.forecast.data import safe_file
from wt_atlas.forecast.inventory import bind_geometry
from wt_atlas.kinetic_missile_points import ADAPTER, DISTANCES
from wt_atlas.research_maps import open_run, save_map


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repository', 'catalog', 'ammunition', 'source-report', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--max-rays', type=int, default=8192)
    parser.add_argument('--maps-dir', type=Path, help='Optional managed offline, reference-only map directory')
    args = parser.parse_args(); started = time.perf_counter()
    if args.max_rays < 1: raise ValueError('Positive ray count required')
    if args.out.resolve() in {p.resolve() for p in (args.catalog, args.ammunition, args.source_report)}:
        raise ValueError('Do not overwrite source inputs')
    report = read_json(args.source_report); source = read_json(args.ammunition)
    if report['output_sha256'] != file_hash(args.ammunition) or report['publishable'] or report['rejected']:
        raise ValueError('Research source report mismatch')
    if report['input_sha256']['catalog'] != file_hash(args.catalog):
        raise ValueError('Research catalog checksum mismatch')
    if report['code_sha256'] != {'adapter': file_hash(ROOT / 'backend/wt_atlas/kinetic_missile_points.py'),
                                 'producer': file_hash(ROOT / 'scripts/prepare_kinetic_missile_points.py')}:
        raise ValueError('Research adapter code checksum mismatch')
    if (len(source) != len(DISTANCES) or {p['distance_m'] for p in source} != set(DISTANCES) or
        any(p.get('reference_only') is not True or p.get('provenance', {}).get('adapter') != ADAPTER for p in source)):
        raise ValueError('Only the four isolated research profiles are supported')
    catalog = read_json(args.catalog)
    selected = {**catalog, 'vehicles': [v for v in catalog['vehicles'] if v['id'] == args.target]}
    bindings, errors = bind_geometry(args.repository, args.repository / 'data/work/client/jobs', selected)
    if errors or set(bindings) != {args.target}: raise ValueError('Independent geometry binding failed')
    identity = bindings[args.target]['identity']
    if any((p['provenance']['client_version'], p['provenance']['datamine_commit']) !=
           (identity['client_version'], identity['datamine_commit']) for p in source):
        raise ValueError('Geometry and projectile source versions differ')
    job = read_json(args.repository / 'data/work/client/jobs' / (args.target + '.json'))
    metadata = safe_file(args.repository, job['layer_metadata'].replace('\\', '/'))
    map_identity = {'target_id': args.target, 'target_sha256': bindings[args.target]['target_sha256'],
                    'layer_metadata_sha256': file_hash(metadata), 'source_profiles_sha256': file_hash(args.ammunition),
                    'layer_arrays_sha256': read_json(metadata)['arrays_sha256'],
                    'source_report_sha256': file_hash(args.source_report), 'backend': 'v2-cuda',
                    'vendor_manifest_sha256': file_hash(ROOT / 'backend/vendor/grp_probability_v2/MANIFEST.json'),
                    'vendor_code_sha256': content_hash({p.relative_to(ROOT / 'backend/vendor/grp_probability_v2').as_posix(): file_hash(p)
                        for p in sorted((ROOT / 'backend/vendor/grp_probability_v2').rglob('*.py'))}),
                    'cuda_adapter_sha256': file_hash(ROOT / 'backend/wt_atlas/cuda_range.py'),
                    'cpu_code_sha256': file_hash(ROOT / 'backend/wt_atlas/cpu_model.py'),
                    'cpu_config_sha256': file_hash(ROOT / 'config/pipeline.json'),
                    'verifier_sha256': file_hash(Path(__file__)),
                    'storage_sha256': file_hash(ROOT / 'backend/wt_atlas/research_maps.py')}
    if args.maps_dir is not None:
        destination = args.maps_dir.resolve()
        for source_root in (args.repository.resolve(), ROOT):
            if destination.is_relative_to(source_root) or source_root.is_relative_to(destination):
                raise ValueError('Keep research maps outside source repositories')
        open_run(args.maps_dir, map_identity)
    cpu = PreparedLayers(metadata, read_json(ROOT / 'config/pipeline.json'))
    gpu = prepare(metadata, ROOT / 'backend/vendor/grp_probability_v2')
    occupied = np.flatnonzero(np.diff(cpu.offsets) > 0)
    if not len(occupied): raise ValueError('Target geometry has no occupied rays')
    ids = np.unique(np.r_[np.linspace(0, cpu.n - 1, min(args.max_rays, cpu.n), dtype=np.int64),
                          occupied[np.linspace(0, len(occupied) - 1, min(args.max_rays, len(occupied)), dtype=np.int64)]])
    rows = []
    for ammo in sorted(source, key=lambda p: p['distance_m']):
        actual = gpu.calculate(ammo); expected = cpu.calculate(ammo, ids)
        probability, charge, state = [v.ravel()[ids] for v in actual]
        np.testing.assert_array_equal(expected[2], state)
        np.testing.assert_allclose(expected[0], probability, rtol=0, atol=2 / 65536, equal_nan=True)
        np.testing.assert_allclose(expected[1], charge, rtol=2e-6, atol=.001, equal_nan=True)
        flags = gpu.risk_flags(ammo)
        np.testing.assert_array_equal(expected[3], flags.ravel()[ids])
        finite = np.isfinite(probability)
        row = {'distance_m': ammo['distance_m'], 'penetration_mm': ammo['penetration_mm'],
                     'inside_declared_distance_bounds': ammo['provenance']['inside_declared_distance_bounds'],
                     'sampled_rays': len(ids), 'computed_rays': int(np.count_nonzero(state == 3)),
                     'partial_probability_rays': int(np.count_nonzero((probability > 0) & (probability < 1))),
                     'max_probability_delta': float(np.max(np.abs(expected[0][finite] - probability[finite]), initial=0))}
        if args.maps_dir is not None:
            saved, reused = save_map(args.maps_dir, map_identity, ammo, (*actual, flags), gpu.plan)
            row['research_map'] = {'map_id': saved['map_id'], 'array_sha256': saved['array_sha256'],
                                   'reused': reused, 'reference_only': True}
        rows.append(row)
    cp = gpu.cp; del gpu, cpu; cp.get_default_memory_pool().free_all_blocks()
    result = {'schema_version': '1.0.0', 'status': 'passed', 'target': args.target,
              'source_profiles_sha256': file_hash(args.ammunition), 'source_report_sha256': file_hash(args.source_report),
              'cpu_code_sha256': file_hash(ROOT / 'backend/wt_atlas/cpu_model.py'),
              'cuda_adapter_sha256': file_hash(ROOT / 'backend/wt_atlas/cuda_range.py'),
              'verification_code_sha256': file_hash(Path(__file__)),
              'layer_metadata_sha256': file_hash(metadata), 'checks': rows, 'duration_seconds': time.perf_counter() - started,
              'source_probability_maps_read': 0, 'game_probability_validated': False, 'formal_rankings': 0,
              'publishable': False, 'scope': 'CPU/V2 implementation parity on conditional exact-distance impact only; does not validate flight, launch eligibility or game probabilities.'}
    write_json(args.out, result)
    print(json.dumps({'status': result['status'], 'checks': len(rows),
                      'sampled_rays': sum(x['sampled_rays'] for x in rows),
                      'max_probability_delta': max(x['max_probability_delta'] for x in rows)}))


if __name__ == '__main__':
    main()
