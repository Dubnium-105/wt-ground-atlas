#!/usr/bin/env python3
"""Import exact client SP multipliers and categories without inventing base SP."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.forecast.native_spawn import import_components
from wt_atlas.common import write_json, file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('catalog', 'rules-root', 'capture', 'out-catalog', 'report'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    if len({p.resolve() for p in (args.catalog, args.out_catalog, args.report)}) != 3:
        raise ValueError('Keep source catalog and outputs separate')
    catalog, report = import_components(args.catalog, args.rules_root, args.capture)
    write_json(args.out_catalog, catalog); report['output_catalog_sha256'] = file_hash(args.out_catalog)
    write_json(args.report, report)
    print(json.dumps({k: report[k] for k in ('counts', 'complete_multipliers', 'generated_final_costs')}, indent=2))


if __name__ == '__main__': main()
