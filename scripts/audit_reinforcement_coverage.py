#!/usr/bin/env python3
"""Cross-check captured configured candidates against the exact profile-gap inventory."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import file_hash, read_json, write_json
from wt_atlas.forecast.reinforcement_coverage import compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('evidence', 'catalog', 'gaps', 'out'): parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(); sources = {k: getattr(args, k) for k in ('evidence', 'catalog', 'gaps')}
    if args.out.resolve() in {p.resolve() for p in sources.values()}: raise ValueError('Output overlaps input')
    hashes = {name: file_hash(path) for name, path in sources.items()}
    evidence, catalog, gaps = (read_json(sources[k]) for k in ('evidence', 'catalog', 'gaps'))
    if hashes['catalog'] != gaps['input_sha256']['catalog']: raise ValueError('Profile-gap catalog hash mismatch')
    result = compare(evidence, catalog, gaps)
    if any(file_hash(path) != hashes[name] for name, path in sources.items()): raise ValueError('Input changed during audit')
    result['input_sha256'] = hashes
    result['code_sha256_lf'] = {name: hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        for name, path in {'audit': ROOT / 'backend/wt_atlas/forecast/reinforcement_coverage.py', 'cli': Path(__file__)}.items()}
    write_json(args.out, result)
    print(json.dumps({k: result[k] for k in ('candidate_count', 'catalog_match_counts', 'exact_profile_gap_count', 'exact_profile_gaps_by_group')}))


if __name__ == '__main__': main()
