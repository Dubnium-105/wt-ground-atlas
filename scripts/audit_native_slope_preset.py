#!/usr/bin/env python3
"""Audit the exact client slope-preset gap without executing the game."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import write_json
from wt_atlas.forecast.native_slope_preset import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('executable', 'capture', 'language-capture', 'catalog', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve() in {p.resolve() for p in (args.executable, args.capture, args.language_capture, args.catalog)}:
        raise ValueError('Audit output must not overwrite an input')
    result = audit(args.executable, args.capture, args.language_capture, args.catalog)
    write_json(args.out, result)
    print(json.dumps({'vehicle_id': result['vehicle_id'], 'projectile': result['projectile'],
                      'slope_response_verified': result['slope_response_verified'],
                      'publishable': result['publishable']}, ensure_ascii=False))


if __name__ == '__main__': main()
