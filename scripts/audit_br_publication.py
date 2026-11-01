#!/usr/bin/env python3
"""Capture linked official BR tables and audit a catalog without changing it."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import read_json, write_json, file_hash
from wt_atlas.forecast.br_publication import capture_publication, verify_capture, parse_sheet, audit_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--source-root', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--announcement-url')
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    if args.out.resolve() == args.catalog.resolve() or args.source_root.resolve() in (args.out.resolve(), *args.out.resolve().parents):
        raise ValueError('Keep catalog and captured source files separate from output')
    capture_publication(args.source_root, args.announcement_url, offline=not args.fetch)
    receipt, payloads = verify_capture(args.source_root)
    report = audit_catalog(read_json(args.catalog), [parse_sheet(payloads[d], d) for d in ('air', 'ground')])
    report.update(source=receipt, input_catalog_sha256=file_hash(args.catalog))
    write_json(args.out, report)
    print(json.dumps({k: report[k] for k in ('counts', 'revision_rows', 'catalog_modified', 'promoted_release_status')}, indent=2))


if __name__ == '__main__':
    main()
