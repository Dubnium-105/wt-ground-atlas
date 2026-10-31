#!/usr/bin/env python3
"""Validate captured official and client random-reinforcement evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import file_hash, write_json
from wt_atlas.forecast.random_reinforcements import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('capture', 'official', 'out'): parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve() in {args.capture.resolve(), args.official.resolve()}:
        raise ValueError('Output must not overwrite source evidence')
    hashes = {p: file_hash(p) for p in (args.capture, args.official)}
    result = audit(args.capture, args.official)
    if any(file_hash(p) != h for p, h in hashes.items()): raise ValueError('Evidence changed during audit')
    result['code_sha256'] = {'audit': file_hash(ROOT / 'backend/wt_atlas/forecast/random_reinforcements.py'),
                             'cli': file_hash(Path(__file__)),
                             'blk_parser_lf': hashlib.sha256((ROOT / 'backend/wt_atlas/native_binding/ordered_blk.py')
                                 .read_bytes().replace(b'\r\n', b'\n')).hexdigest()}
    write_json(args.out, result)
    print(json.dumps({'status': 'source_evidence_only', 'version': result['source_version'],
                      'runtime_session_state_observed': False, 'publishable': False}))


if __name__ == '__main__': main()
