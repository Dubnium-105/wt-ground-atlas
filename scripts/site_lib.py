"""Standard-library helpers shared by static assembly and release packaging.

Only the generated site is a deployment input. Private working trees and
probability exports must never be discovered by recursively packaging the repo.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from urllib.parse import urlsplit, unquote

MARKER = '.wt-atlas-site'
DEFAULT_BUDGETS = {'site_bytes': 100 * 1024 * 1024, 'file_bytes': 25 * 1024 * 1024}


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def file_hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def deployment_settings(root: Path) -> dict:
    path = root / 'config/deployment.json'
    settings = read_json(path) if path.exists() else {}
    return {
        'probability': {'mode': 'disabled', 'base_url': '', **settings.get('probability', {})},
        'budgets': {**DEFAULT_BUDGETS, **settings.get('budgets', {})},
    }


def site_directory(root: Path, output: Path | None = None) -> Path:
    configured = output or read_json(root / 'config/hosting.json').get('public_directory', 'dist/site')
    path = Path(configured)
    return (path if path.is_absolute() else root / path).resolve()


def probability_directory(root: Path) -> Path:
    config = read_json(root / 'config/pipeline.json')
    path = Path(config.get('paths', {}).get('static_probability', 'data/exports/probability'))
    return (path if path.is_absolute() else root / path).resolve()


def local_file(directory: Path, relative: str) -> Path:
    """Resolve only a plain local relative URL; never follow a symlink."""
    if not isinstance(relative, str):
        raise ValueError(f'Expected a local relative file: {relative!r}')
    parsed = urlsplit(relative)
    path = PurePosixPath(relative)
    if (not relative or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
            or path.is_absolute() or '..' in path.parts or '\\' in relative
            or unquote(relative) != relative):
        raise ValueError(f'Unsafe local data path: {relative!r}')
    base = directory.resolve()
    candidate = directory / path
    current = directory
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'Symlinks are not deployment inputs: {relative}')
    if not candidate.resolve().is_relative_to(base) or not candidate.is_file():
        raise FileNotFoundError(f'Missing local data file: {candidate}')
    return candidate


def probability_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.netloc
            or not parsed.hostname or parsed.username or parsed.password
            or '?' in value or '#' in value or '\\' in value
            or any(character.isspace() for character in value)):
        raise ValueError('External probability mode requires an absolute HTTP(S) directory URL without credentials, query or fragment.')
    # Accessing port also rejects a malformed authority such as host:invalid.
    _ = parsed.port
    return value.rstrip('/') + '/'


def copy_sources(root: Path, destination: Path) -> None:
    allowed = {'.html', '.css', '.js', '.mjs', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.ico', '.woff', '.woff2', '.txt', '.json'}
    for directory, directories, filenames in os.walk(root / 'web', followlinks=False):
        directories[:] = [name for name in directories if name not in ('data', 'shared') and not name.startswith('.')]
        source_dir = Path(directory)
        for name in directories:
            if (source_dir / name).is_symlink():
                raise ValueError(f'Symlink in web sources: {source_dir / name}')
        for name in filenames:
            source = source_dir / name
            if name.startswith('.') or source.suffix not in allowed:
                continue
            if source.is_symlink():
                raise ValueError(f'Symlink in web sources: {source}')
            target = destination / source.relative_to(root / 'web')
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix in ('.mjs', '.js'):
                # Source modules import the canonical shared/ sibling. The
                # deployable site owns a generated copy at its own shared/.
                code = source.read_text(encoding='utf-8')
                code = code.replace("'../shared/", "'./shared/").replace('"../shared/', '"./shared/')
                target.write_text(code, encoding='utf-8')
            else:
                shutil.copy2(source, target)
    for source in (root / 'shared').glob('*.mjs'):
        if source.is_symlink():
            raise ValueError(f'Symlink in shared sources: {source}')
        target = destination / 'shared' / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def referenced_probability_files(index: dict) -> set[str]:
    files = set()
    for entry in index.get('entries', []):
        for shard in entry['shards']:
            files.add(shard['url'])
            for key in ('gzip_url', 'preview_url'):
                if shard.get(key):
                    files.add(shard[key])
    return files


def copy_probability(source: Path, destination: Path, index: dict) -> None:
    for relative in sorted(referenced_probability_files(index)):
        path = local_file(source, relative)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    raw = local_file(source, 'index.json').read_bytes()
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'index.json').write_bytes(raw)
    (destination / 'index.json.gz').write_bytes(gzip.compress(raw, mtime=0))


def validate_site(site: Path, budgets: dict | None = None) -> dict:
    """Check a bounded deploy tree without inspecting any compute directory."""
    initial_manifest=read_json(site/'data/manifest.json') if (site/'data/manifest.json').exists() else {}
    unified=initial_manifest.get('kind')=='unified_forecast_site'
    required = ('index.html','app.mjs','index.json','research/index.html') if unified else ('index.html', 'app.mjs', 'worker.mjs', 'data-client.mjs',
                'shared/engine.mjs', 'shared/probability-codec.mjs', 'data/manifest.json')
    for name in required:
        local_file(site, name)
    manifest = read_json(site / 'data/manifest.json')
    if unified:
        index=read_json(local_file(site,'index.json'))
        def forecast_descriptor(d,directory):
            source=local_file(directory,d['url'])
            if source.stat().st_size!=d['bytes'] or file_hash(source)!=d['sha256']:raise ValueError('Forecast descriptor checksum failure')
            if d.get('gzip_url'):
                compressed=local_file(directory,d['gzip_url'])
                with gzip.open(compressed,'rb') as stream:expanded=stream.read(d['bytes']+1)
                if compressed.stat().st_size!=d['gzip_bytes'] or expanded!=source.read_bytes():raise ValueError('Forecast gzip mismatch')
            return read_json(source)
        directories={site}
        for cohort in index.get('cohorts',[]):directories.add(local_file(site,cohort['directory']+'index.json').parent)
        for directory in directories:
            ci=read_json(local_file(directory,'index.json'))
            forecast_descriptor(ci['report'],directory)
            migration_index=forecast_descriptor(ci['migrations'],directory)
            if isinstance(migration_index,dict) and migration_index.get('kind')=='migration_index':
                for shard in migration_index['tables']:forecast_descriptor(shard,directory)
            for d in ci['worlds'].values():
                world=forecast_descriptor(d,directory)
                for shard in world['tables']:forecast_descriptor(shard,directory)
                for boards in world.get('views',{}).values():
                    for descriptor in boards.values():forecast_descriptor(descriptor,directory)
        if index['research']['status']=='available':validate_site(site/'research',budgets)
    mode = manifest.get('deployment', {}).get('probability_mode', 'disabled')
    paths = []
    for directory, directories, filenames in os.walk(site, followlinks=False):
        for name in directories:
            if (Path(directory) / name).is_symlink():
                raise ValueError(f'Symlink in site output: {name}')
        for name in filenames:
            if name == MARKER:
                continue
            path = Path(directory) / name
            relative = path.relative_to(site)
            if (path.is_symlink() or any(part.startswith('.') for part in relative.parts)
                    or path.suffix in ('.npz', '.grp', '.exe', '.py', '.zip')
                    or relative.parts[0] in ('backend', 'config', 'reports', 'node_modules')):
                raise ValueError(f'Non-public file in site output: {relative}')
            if mode != 'embedded' and relative.parts[:2] == ('data', 'probability'):
                raise ValueError('Lightweight site contains a probability export; use external mode or explicitly select embedded mode.')
            paths.append(path)
    def check_descriptors(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('url', 'gzip_url') and isinstance(item, str) and item.startswith('data/'):
                    local_file(site, item)
                elif key not in ('sources',):
                    check_descriptors(item)
        elif isinstance(value, list):
            for item in value:
                check_descriptors(item)
    check_descriptors(manifest)
    limits = {**DEFAULT_BUDGETS, **(budgets or {})}
    for name, value in limits.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f'Invalid deployment budget: {name}={value!r}')
    sizes = [(path, path.stat().st_size) for path in paths]
    total = sum(size for _, size in sizes)
    if limits['site_bytes'] and total > limits['site_bytes']:
        raise ValueError(f'Site byte budget exceeded: {total} > {limits["site_bytes"]}. Keep probability exports external or explicitly change config/deployment.json.')
    oversized = [(path.relative_to(site).as_posix(), size) for path, size in sizes if limits['file_bytes'] and size > limits['file_bytes']]
    if oversized:
        raise ValueError(f'Site file byte budget exceeded: {oversized[:5]}')
    return {'static_total_bytes': total, 'static_published_file_count': len(paths), 'probability_mode': mode, 'budgets': limits}
