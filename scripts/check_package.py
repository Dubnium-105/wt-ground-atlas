#!/usr/bin/env python3
"""Extract and validate a static release independently of compute inputs."""
from pathlib import Path, PurePosixPath
import argparse
import json
import tempfile
import zipfile

from site_lib import deployment_settings, validate_site
from verify_release import verify

PROJECT = Path(__file__).resolve().parents[1]


def check(archive_path: Path, root: Path=PROJECT) -> dict:
    with tempfile.TemporaryDirectory(prefix='wt-atlas-release-check-') as directory:
        destination = Path(directory)
        with zipfile.ZipFile(archive_path) as archive:
            names = set()
            for entry in archive.infolist():
                name = PurePosixPath(entry.filename)
                if (name.is_absolute() or '..' in name.parts or '\\' in entry.filename
                        or entry.filename in names or ((entry.external_attr >> 16) & 0o170000) == 0o120000):
                    raise ValueError(f'Unsafe archive entry: {entry.filename}')
                names.add(entry.filename)
            corrupt = archive.testzip()
            if corrupt:
                raise ValueError(f'Invalid ZIP CRC: {corrupt}')
            archive.extractall(destination)
        integrity = verify(destination)
        if integrity['failures']:
            raise ValueError(f'Release integrity failures: {integrity["failures"]}')
        validation = validate_site(destination, deployment_settings(root)['budgets'])
        return {'cold_extraction_passed': True, 'manifest_verification': integrity,
                **validation, 'scope': 'Static artifact integrity; remote CDN availability is checked separately.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--report', type=Path, default=PROJECT / 'reports/release-package-check.json')
    args = parser.parse_args()
    report = check(args.archive)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
