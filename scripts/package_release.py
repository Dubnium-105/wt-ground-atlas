#!/usr/bin/env python3
"""Package only an already-built static site, with streaming checksums and ZIP64."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_lib import MARKER, deployment_settings, file_hash, site_directory, validate_site


def package(root: Path, output: Path, site: Path | None = None) -> dict:
    root, output = Path(root).resolve(), Path(output).resolve()
    public = site_directory(root, site)
    if output.is_relative_to(public):
        raise ValueError('The release archive must be outside the generated site directory.')
    validation = validate_site(public, deployment_settings(root)['budgets'])
    paths = sorted(path for path in public.rglob('*') if path.is_file() and path.name not in (MARKER, 'FILE_INTEGRITY.sha256'))
    checksums = '\n'.join(f'{file_hash(path)}  {path.relative_to(public).as_posix()}' for path in paths) + '\n'
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=output.stem + '-', suffix='.partial', dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for path in paths:
                compression = zipfile.ZIP_STORED if path.suffix in ('.gz', '.woff2', '.png', '.jpg', '.webp') else zipfile.ZIP_DEFLATED
                archive.write(path, path.relative_to(public).as_posix(), compress_type=compression)
            archive.writestr('FILE_INTEGRITY.sha256', checksums)
        with zipfile.ZipFile(temporary) as archive:
            corrupt = archive.testzip()
            if corrupt:
                raise ValueError(f'ZIP CRC validation failed: {corrupt}')
            entries = len(archive.infolist())
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    report = {
        'file': str(output), 'bytes': output.stat().st_size, 'sha256': file_hash(output),
        'entries': entries, 'uncompressed_bytes': validation['static_total_bytes'],
        'probability_mode': validation['probability_mode'], 'crc_validation': 'all_entries_passed',
        'checksum_manifest': 'FILE_INTEGRITY.sha256', 'scope': 'deployable_static_site_only',
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--site', type=Path, help='Already-built site; defaults to config/hosting.json.')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    try:
        package(options.root, options.output, options.site)
    except (FileNotFoundError, ValueError) as error:
        parser.exit(1, str(error) + '\n')
