#!/usr/bin/env python3
"""Verify every source and published data file against the release SHA-256 manifest."""
from pathlib import Path
import hashlib
import json
import sys

root = Path(__file__).resolve().parents[1]
manifest = root / 'FILE_INTEGRITY.sha256'
if not manifest.exists():
    raise SystemExit('FILE_INTEGRITY.sha256 is generated inside the completed release ZIP.')
failures = []
checked = 0
for line in manifest.read_text(encoding='utf-8').splitlines():
    expected, relative = line.split('  ', 1)
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        failures.append({'file': relative, 'error': 'missing_or_unsafe_path'})
        continue
    with path.open('rb') as handle:
        actual = hashlib.file_digest(handle, 'sha256').hexdigest()
    checked += 1
    if actual != expected:
        failures.append({'file': relative, 'error': 'hash_mismatch'})
print(json.dumps({'checked': checked, 'failures': failures}, ensure_ascii=False, indent=2))
sys.exit(bool(failures))
