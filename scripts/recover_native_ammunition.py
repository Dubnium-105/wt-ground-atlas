#!/usr/bin/env python3
"""Recover exact mounted ammunition using captured client localization."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.ammunition_recovery import recover
from wt_atlas.common import write_json, file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('catalog', 'ammunition', 'capture', 'language-capture', 'wiki-root', 'rules-root', 'out', 'out-catalog', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--vehicles', nargs='+')
    parser.add_argument('--default-belts', action='store_true', help='Verify captured GUI rules and exact parent weapon names for default belts')
    args = parser.parse_args()
    inputs = {p.resolve() for p in (args.catalog, args.ammunition, args.capture, args.language_capture)}
    outputs = {p.resolve() for p in (args.out, args.out_catalog, args.report)}
    if len(outputs) != 3 or inputs & outputs:
        raise ValueError('Keep recovery outputs distinct from each other and from source inputs')
    catalog, ammunition, report = recover(args.catalog, args.ammunition, args.capture,
        args.language_capture, args.wiki_root, args.rules_root, vehicle_ids=set(args.vehicles or []), default_belts=args.default_belts)
    write_json(args.out, ammunition); write_json(args.out_catalog, catalog)
    report.update(output_ammunition_sha256=file_hash(args.out), output_catalog_sha256=file_hash(args.out_catalog))
    write_json(args.report, report)
    print(json.dumps({k: report[k] for k in ('counts', 'unresolved_reasons', 'vehicle_errors')}, indent=2))


if __name__ == '__main__': main()
