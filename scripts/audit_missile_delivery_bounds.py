#!/usr/bin/env python3
"""Audit existing source-distance profiles without opening probability maps."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import file_hash, read_json, write_json
from wt_atlas.forecast.delivery import audit_profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('profiles', 'config', 'receipt', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    inputs = {'profiles': args.profiles, 'config': args.config, 'receipt': args.receipt}
    if args.out.resolve() in {p.resolve() for p in inputs.values()}:
        raise ValueError('Do not overwrite source inputs')
    hashes = {name: file_hash(path) for name, path in inputs.items()}
    receipt = read_json(args.receipt)
    if (receipt['input_sha256']['ammunition'] != hashes['profiles'] or
        receipt['input_sha256']['config'] != hashes['config']):
        raise ValueError('Profiles or scenario config do not match the source receipt')
    profiles, config = read_json(args.profiles), read_json(args.config)
    if any((p.get('provenance', {}).get('client_version'), p.get('provenance', {}).get('datamine_commit')) !=
           tuple(receipt['source_version']) for p in profiles):
        raise ValueError('Profile source version mismatch')
    result = audit_profiles(profiles, config)
    if any(file_hash(path) != hashes[name] for name, path in inputs.items()):
        raise ValueError('Source changed during audit')
    result.update(schema_version='1.0.0', source_version=receipt['source_version'], input_sha256=hashes,
        code_sha256={'delivery': file_hash(ROOT / 'backend/wt_atlas/forecast/delivery.py'),
                     'audit': file_hash(Path(__file__))},
        scope='Source profile audit only; arrays, legal variants and actual flight are not checked. Counts overlap other data gaps. Outside a native declaration means delivery is unknown, not that firing is impossible.',
        arrays_checked=False, publishable=False, formal_rankings=0)
    write_json(args.out, result)
    print(json.dumps({k: result[k] for k in ('affected_profiles', 'distinct_ammo_ids', 'distinct_vehicles', 'profiles_by_distance_m')}))


if __name__ == '__main__': main()
