#!/usr/bin/env python3
"""Verify an extracted site against its streaming SHA-256 manifest."""
from pathlib import Path
import argparse
import json
import sys

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_lib import file_hash, local_file


def verify(root: Path) -> dict:
    manifest = root / 'FILE_INTEGRITY.sha256'
    if not manifest.exists():
        raise FileNotFoundError('FILE_INTEGRITY.sha256 is generated inside the site release ZIP.')
    failures, checked, names = [], 0, set()
    for line in manifest.read_text(encoding='utf-8').splitlines():
        try:
            expected, relative = line.split('  ', 1)
            if relative in names:
                raise ValueError('duplicate manifest entry')
            names.add(relative)
            path = local_file(root, relative)
            if len(expected) != 64 or file_hash(path) != expected:
                raise ValueError('hash_mismatch')
            checked += 1
        except (ValueError, FileNotFoundError) as error:
            failures.append({'entry': line, 'error': str(error)})
    if not names:
        failures.append({'error': 'empty checksum manifest'})
    extras = sorted(path.relative_to(root).as_posix() for path in root.rglob('*')
                    if path.is_file() and path.name != 'FILE_INTEGRITY.sha256' and path.relative_to(root).as_posix() not in names)
    failures.extend({'file': name, 'error': 'unlisted_file'} for name in extras)
    return {'checked': checked, 'failures': failures}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='Extracted site directory.')
    args = parser.parse_args()
    try:
        report = verify(args.root.resolve())
    except (ValueError, FileNotFoundError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(bool(report['failures']))
