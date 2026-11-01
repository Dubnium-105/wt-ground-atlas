#!/usr/bin/env python3
"""Produce a source-checked per-vehicle ammunition gap inventory."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import atomic_write, content_hash, file_hash, read_json
from wt_atlas.remaining_ammunition import reconcile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('gaps', 'summary', 'recovery', 'catalog', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    paths = {name: getattr(args, name) for name in ('gaps', 'summary', 'recovery', 'catalog')}
    if args.out.resolve() in {p.resolve() for p in paths.values()}:
        raise ValueError('Output must not overwrite an input')
    hashes = {name: file_hash(path) for name, path in paths.items()}
    values = {name: read_json(path) for name, path in paths.items()}
    if hashes['recovery'] != values['summary']['source_recovery_sha256'] or hashes['catalog'] != values['summary']['catalog_sha256']:
        raise ValueError('Recovery or catalog checksum mismatch')
    if hashes['summary'] != values['gaps']['ground_ammunition']['remaining_breakdown']['report_sha256']:
        raise ValueError('Gap summary checksum mismatch')
    result = reconcile(values['gaps'], values['summary'], values['recovery'], values['catalog'])
    if any(file_hash(path) != hashes[name] for name, path in paths.items()):
        raise ValueError('Input changed during reconciliation')
    gap_scope = {'source_version': values['gaps']['source_version'],
                 'vehicle_ids': values['gaps']['ground_ammunition']['vehicles_without_any_exact_owned_profile'],
                 'summary_sha256': values['gaps']['ground_ammunition']['remaining_breakdown']['report_sha256']}
    result['input_sha256'] = {name: value for name, value in hashes.items() if name != 'gaps'}
    result['input_sha256']['gaps_scope'] = content_hash(gap_scope)
    atomic_write(args.out, (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8'))
    print(json.dumps({k: result[k] for k in ('vehicle_count', 'vehicles_with_unresolved_records',
                                             'vehicles_without_unresolved_records', 'unresolved_selection_records')}))


if __name__ == '__main__':
    main()
