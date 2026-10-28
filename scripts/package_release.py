#!/usr/bin/env python3
"""Create and validate a complete release archive, excluding only reproducible caches."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_ROOTS = (
    'data/raw', 'data/work', 'data/cache', 'reports/screenshots',
    '.git', '.venv', 'node_modules',
)
EXCLUDED_PARTS = {'__pycache__', '.pytest_cache'}
REQUIRED = (
    'README.md', 'build.py', 'run-local.cmd', 'run-local.sh',
    'web/index.html', 'web/app.mjs', 'web/worker.mjs', 'web/data-client.mjs',
    'web/shared/engine.mjs', 'web/data/manifest.json',
    'web/data/probability/index.json', 'web/data/recommendations/index.json',
    'backend/wt_atlas/__main__.py', 'config/model.json', 'config/pipeline.json',
    'data/snapshots/catalog.json', 'data/snapshots/changes.json',
    'data/snapshots/decision-probabilities.json', 'data/snapshots/target-layers.json',
    'docs/ACCEPTANCE.md', 'docs/PIPELINE.md', 'reports/browser-e2e.json',
    'backend/vendor/grp_probability_v2/data/runtime/loader.json',
    'backend/vendor/grp_probability_v2/data/runtime/native_resource_blas_loader_validation.cases.jsonl.gz',
    'backend/vendor/grp_probability_v2/data/runtime/original_blas_resource_full.chunks.bin',
)


def included(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    name = relative.as_posix()
    return (
        path.is_file() and not path.is_symlink()
        and not EXCLUDED_PARTS.intersection(relative.parts)
        and path.suffix not in ('.pyc', '.pyo', '.zip', '.partial')
        and name != 'FILE_INTEGRITY.sha256'
        and not any(name == prefix or name.startswith(prefix + '/') for prefix in EXCLUDED_ROOTS)
    )


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def package(root: Path, output: Path) -> dict:
    # Assemble in this process immediately before enumerating files, so the
    # archive cannot retain obsolete generated shards from an earlier release.
    spec = importlib.util.spec_from_file_location('release_web_builder', root / 'scripts/build_web.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.build(root)
    manifest = json.loads((root / 'web/data/manifest.json').read_text(encoding='utf-8'))
    active_urls = set()
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('url', 'gzip_url') and isinstance(item, str) and item.startswith('data/'):
                    active_urls.add(item)
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(manifest)
    generated = {'config', 'catalog', 'nations', 'vehicles', 'scenarios', 'impact', 'reports', 'physics'}
    rec_index = json.loads((root / 'web/data/recommendations/index.json').read_text(encoding='utf-8'))
    rec_files = {'index.json', 'build-report.json'}
    for entry in rec_index['files']:
        rec_files.update((entry['url'], entry['url'] + '.gz', entry['alias']))
    def published(path):
        relative = path.relative_to(root)
        if len(relative.parts) > 3 and relative.parts[:2] == ('web', 'data'):
            if relative.parts[2] in generated:
                return Path(*relative.parts[1:]).as_posix() in active_urls
            if relative.parts[2] == 'recommendations':
                return path.name in rec_files
        return True
    paths = sorted(path for path in root.rglob('*') if included(path, root) and published(path))
    names = {path.relative_to(root).as_posix() for path in paths}
    missing = sorted(set(REQUIRED) - names)
    if missing:
        raise FileNotFoundError(f'Incomplete release: {missing}')
    browser = json.loads((root / 'reports/browser-e2e.json').read_text())
    if not browser.get('tests') or any(test['status'] != 'passed' for test in browser['tests']):
        raise ValueError('Complete browser acceptance is required before packaging.')
    checksums = '\n'.join(f'{digest(path)}  {path.relative_to(root).as_posix()}' for path in paths) + '\n'
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=output.stem + '-', suffix='.partial', dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
            for path in paths:
                archive.write(path, (Path(root.name) / path.relative_to(root)).as_posix())
            archive.writestr(f'{root.name}/FILE_INTEGRITY.sha256', checksums)
        with zipfile.ZipFile(temporary) as archive:
            corrupt = archive.testzip()
            if corrupt:
                raise ValueError(f'ZIP CRC validation failed: {corrupt}')
            archived = set(archive.namelist())
            if not {f'{root.name}/{name}' for name in REQUIRED}.issubset(archived):
                raise ValueError('ZIP is missing required entry points.')
            entries = len(archived)
            uncompressed = sum(entry.file_size for entry in archive.infolist())
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    report = {
        'file': output.name, 'bytes': output.stat().st_size,
        'sha256': digest(output), 'entries': entries,
        'uncompressed_bytes': uncompressed, 'crc_validation': 'all_entries_passed',
        'required_files': 'all_present', 'checksum_manifest': 'FILE_INTEGRITY.sha256',
        'cache_exclusions': list(EXCLUDED_ROOTS),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    package(options.root.resolve(), options.output.resolve())
