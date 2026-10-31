#!/usr/bin/env python3
"""Verify the captured native projectile-field loader without executing the game."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import file_hash, write_json
from wt_atlas.forecast.native_bullet_count import audit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('executable', 'capture', 'out'): p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    if args.out.resolve() in {args.executable.resolve(), args.capture.resolve()}:
        raise ValueError('Output overlaps source')
    before = file_hash(args.capture); result = audit(args.executable, args.capture)
    if file_hash(args.capture) != before: raise ValueError('Capture changed during audit')
    sources = {'audit': ROOT / 'backend/wt_atlas/forecast/native_bullet_count.py',
               'cli': Path(__file__), 'blk_parser': ROOT / 'backend/wt_atlas/native_binding/ordered_blk.py'}
    result['code_sha256_lf'] = {k: hashlib.sha256(v.read_bytes().replace(b'\r\n', b'\n')).hexdigest() for k,v in sources.items()}
    write_json(args.out, result)
    print(json.dumps({'source_version': result['source_version'], 'missing_field_read_default': 0,
                      'firing_order': 'unverified', 'vehicles': len(result['vehicles'])}))


if __name__ == '__main__': main()
