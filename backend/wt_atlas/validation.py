"""Repeat numerical comparison against the user's original probability atlas."""
from __future__ import annotations
import argparse
from pathlib import Path
import time
import numpy as np
from .common import load_config, read_json, write_json
from .cpu_model import PreparedLayers


def validate_cpu_reference(input_root, config, sampled_rays, output):
    input_root = Path(input_root)
    ammo = read_json(input_root / 'provenance/ammunition.json')
    reports = []
    for metadata in sorted((input_root / 'geometry_layers').glob('*.layers.json')):
        started = time.perf_counter()
        model = PreparedLayers(metadata, config)
        ray_ids = np.unique(np.linspace(0, model.n - 1, min(sampled_rays, model.n), dtype=np.int64))
        for profile in ammo:
            result = model.calculate(profile, ray_ids)
            reference = input_root / 'arrays' / (model.meta['vehicle'] + '.' + profile['name'] + '.npz')
            with np.load(reference, allow_pickle=False) as packet:
                expected = packet['penetration_probability'].ravel()[ray_ids]
                finite = np.isfinite(result[0]) & np.isfinite(expected)
                error = np.abs(result[0][finite] - expected[finite])
                reports.append({'target': model.meta['vehicle'], 'ammo': profile['name'], 'sampled_rays': len(ray_ids),
                    'computed': int(finite.sum()), 'status_mismatches': int(np.sum(result[2] != packet['status'].ravel()[ray_ids])),
                    'flags_mismatches': int(np.sum(result[3] != packet['material_response_flags'].ravel()[ray_ids])),
                    'max_probability_abs_error': float(error.max(initial=0)),
                    'mean_probability_abs_error': float(error.mean()) if len(error) else None,
                    'elapsed_s': time.perf_counter() - started})
    if not reports: raise ValueError('No source layer/reference pairs found')
    report = {'method': 'Deterministic evenly spaced source ray IDs; source V2 CPU implementation comparison, not game validation', 'results': reports}
    write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config/pipeline.json')
    parser.add_argument('--atlas', required=True)
    parser.add_argument('--rays', type=int)
    parser.add_argument('--report', default='reports/cpu-model-validation.json')
    args = parser.parse_args()
    config, root = load_config(args.config)
    rays = args.rays or config['validation']['sample_rays']
    if rays <= 0: raise ValueError('Sample ray count must be positive')
    report = validate_cpu_reference(args.atlas, config, rays, args.report)
    import json
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
