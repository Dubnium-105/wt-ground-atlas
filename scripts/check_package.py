#!/usr/bin/env python3
"""Extract a release in a clean directory, verify all hashes and rebuild it offline."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--archive', type=Path, required=True)
parser.add_argument('--report', type=Path, default=PROJECT / 'reports/release-package-check.json')
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='wt-ground-atlas-check-') as directory:
    destination = Path(directory)
    with zipfile.ZipFile(args.archive) as archive:
        for entry in archive.infolist():
            if not (destination / entry.filename).resolve().is_relative_to(destination):
                raise ValueError(f'Unsafe archive path: {entry.filename}')
        archive.extractall(destination)
    roots = [path for path in destination.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise ValueError('Release must have one project root.')
    root = roots[0]
    env = os.environ.copy()
    env['PYTHONPATH'] = str(root / 'backend')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    def call(command):
        start = time.perf_counter()
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=True)
        return result, time.perf_counter() - start
    verified, _ = call([sys.executable, 'scripts/verify_release.py'])
    cli, _ = call([sys.executable, '-m', 'wt_atlas', '--help'])
    def public_hashes():
        return {path.relative_to(root / 'web').as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (root / 'web').rglob('*') if path.is_file()}
    before = public_hashes()
    rebuilt, elapsed = call([sys.executable, 'build.py'])
    after = public_hashes()
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    if changed:
        raise ValueError(f'Cold rebuild changed published assets: {changed}')
    report = {
        'cold_extraction_passed': True,
        'manifest_verification': json.loads(verified.stdout),
        'backend_cli_import_exit_code': cli.returncode,
        'rebuild_exit_code': rebuilt.returncode,
        'rebuild_duration_s': elapsed,
        'rebuild_cache_unchanged': '"status":"unchanged"' in rebuilt.stdout.replace(' ', ''),
        'public_static_file_count': len(after),
        'public_static_files_changed_on_cold_rebuild': changed,
        'source_cache_included': (root / 'data/raw').exists(),
        'work_cache_included': (root / 'data/work').exists(),
        'screenshots_included': (root / 'reports/screenshots').exists(),
        'vendor_files': sum(path.is_file() for path in (root / 'backend/vendor/grp_probability_v2').rglob('*')),
        'public_manifest_sha256': hashlib.sha256((root / 'web/data/manifest.json').read_bytes()).hexdigest(),
        'scope': 'Cold check of runtime/source payload before final acceptance receipt; final archive separately validates every CRC and contains per-file SHA-256.',
    }
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
