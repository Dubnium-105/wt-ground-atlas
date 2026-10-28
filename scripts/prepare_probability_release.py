#!/usr/bin/env python3
"""Verify an immutable probability release without copying its large shards.

The report contains the exact referenced file list for a separate object-store
upload. Upload data files first and index files last, under release_id/. No
network calls are made, and unreferenced historical shards are not included.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CHUNK_BYTES = 1024 * 1024


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b''):
            digest.update(block)
    return digest.hexdigest()


def _local_file(source, name):
    if (not isinstance(name, str) or not name or '\\' in name or '%' in name
            or any(character in name for character in (':', '?', '#', '\0'))
            or PurePosixPath(name).is_absolute() or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Expected a safe relative release path: ' + repr(name))
    candidate = source / name
    if not candidate.resolve().is_relative_to(source):
        raise ValueError('Release path escapes the probability directory: ' + name)
    cursor = candidate
    while cursor != source:
        if cursor.is_symlink():
            raise ValueError('Release files must not be symlinks: ' + name)
        cursor = cursor.parent
    if not candidate.is_file():
        raise FileNotFoundError('Missing probability release file: ' + name)
    return candidate


def _check_digest(value, name):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Missing or invalid SHA256: ' + name)


def _check_gzip(path, expected_sha256, expected_bytes):
    """Bound decoded memory and abort expansion beyond the declared size."""
    digest = hashlib.sha256()
    total = 0
    with gzip.open(path, 'rb') as stream:
        while True:
            block = stream.read(min(CHUNK_BYTES, expected_bytes - total + 1))
            if not block:
                break
            total += len(block)
            if total > expected_bytes:
                raise ValueError('Gzip expands beyond declared byte count: ' + str(path))
            digest.update(block)
    if total != expected_bytes or digest.hexdigest() != expected_sha256:
        raise ValueError('Gzip does not match the referenced raw file: ' + str(path))


def _write_atomic(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.part', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_release(source, report=None, list_files=None):
    source = Path(source).resolve()
    outputs = [Path(value).resolve() for value in (report, list_files) if value is not None]
    if len(outputs) != len(set(outputs)) or any(path.is_relative_to(source) for path in outputs):
        raise ValueError('Write the report and upload list to distinct paths outside the immutable probability directory')
    index_path = _local_file(source, 'index.json')
    index_bytes = index_path.read_bytes()
    index_sha256 = hashlib.sha256(index_bytes).hexdigest()
    index = json.loads(index_bytes)
    if index.get('schema_version') != 1 or index.get('kind') != 'conditional-probability-atlas':
        raise ValueError('Expected conditional-probability-atlas schema 1')
    if not isinstance(index.get('entries'), list) or not index['entries']:
        raise ValueError('Probability index contains no entries')
    files = {}

    def verify(name, digest=None, size=None):
        if digest is not None:
            _check_digest(digest, name)
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise ValueError('Invalid byte count: ' + name)
        if name in files:
            previous = files[name]
            if (digest is not None and previous['sha256'] != digest) or (size is not None and previous['bytes'] != size):
                raise ValueError('Conflicting metadata for one release file: ' + name)
            return previous
        path = _local_file(source, name)
        actual_size = path.stat().st_size
        if size is not None and actual_size != size:
            raise ValueError('Probability byte-count mismatch: ' + name)
        actual_digest = _sha256(path)
        if digest is not None and actual_digest != digest:
            raise ValueError('Probability hash mismatch: ' + name)
        files[name] = {'path': name, 'sha256': actual_digest, 'bytes': actual_size}
        return files[name]

    entries = set()
    gzip_verified = set()
    for entry in index['entries']:
        if not isinstance(entry, dict) or not entry.get('id') or entry['id'] in entries:
            raise ValueError('Missing or duplicate probability entry ID')
        entries.add(entry['id'])
        angles, shards = entry.get('angles_deg'), entry.get('shards')
        if not isinstance(angles, list) or not isinstance(shards, list) or not angles or len(angles) != len(shards) or len(set(angles)) != len(angles):
            raise ValueError('Angles / shards metadata mismatch')
        width, height = entry.get('width'), entry.get('height')
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in (width, height)):
            raise ValueError('Invalid probability grid dimensions')
        seen_angles = set()
        for shard in shards:
            angle_index = shard.get('angle_index')
            if (not isinstance(angle_index, int) or isinstance(angle_index, bool) or angle_index in seen_angles
                    or not 0 <= angle_index < len(angles) or angles[angle_index] != shard.get('angle_deg')):
                raise ValueError('Shard angle/index mismatch')
            seen_angles.add(angle_index)
            _check_digest(shard.get('sha256'), shard.get('url', '<missing>'))
            if shard.get('bytes') != width * height * 4:
                raise ValueError('Probability byte-count does not match grid dimensions')
            raw = verify(shard.get('url'), shard['sha256'], shard['bytes'])
            if shard.get('gzip_url'):
                _check_digest(shard.get('gzip_sha256'), shard['gzip_url'])
                compressed = verify(shard['gzip_url'], shard['gzip_sha256'], shard.get('gzip_bytes'))
                pair = (compressed['path'], raw['sha256'])
                if pair not in gzip_verified:
                    _check_gzip(_local_file(source, compressed['path']), raw['sha256'], raw['bytes'])
                    gzip_verified.add(pair)
            if shard.get('preview_url'):
                verify(shard['preview_url'])
    # A concurrently replaced index must not produce a mixed release manifest.
    if _sha256(index_path) != index_sha256:
        raise ValueError('Probability index changed while verifying the release')
    index_uploads = []
    if (source / 'index.json.gz').exists():
        compressed_index = verify('index.json.gz')
        _check_gzip(_local_file(source, 'index.json.gz'), index_sha256, len(index_bytes))
        index_uploads.append(compressed_index)
    index_uploads.append(verify('index.json', index_sha256, len(index_bytes)))
    index_names = {item['path'] for item in index_uploads}
    ordered_files = [files[name] for name in sorted(files) if name not in index_names] + index_uploads
    result = {
        'schema_version': 1,
        'release_id': index_sha256,
        'index_sha256': index_sha256,
        'source_version': index.get('source_version'),
        'file_count': len(ordered_files),
        'total_bytes': sum(item['bytes'] for item in ordered_files),
        'entry_count': len(entries),
        'all_referenced_hashes_verified': True,
        'gzip_payloads_verified': True,
        'upload_order': 'listed order: shards and previews first, indexes last',
        'publish_prefix': 'probability/' + index_sha256 + '/',
        'files': ordered_files,
    }
    # Reports are written only after all files and compressed payloads pass.
    if report is not None:
        _write_atomic(report, (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    if list_files is not None:
        _write_atomic(list_files, (''.join(item['path'] + '\n' for item in ordered_files)).encode('utf-8'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'data/exports/probability')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/probability-release.json')
    parser.add_argument('--list-files', type=Path, help='Optional newline-separated, relative upload file list')
    args = parser.parse_args()
    result = prepare_release(args.source, args.report, args.list_files)
    print(json.dumps({key: value for key, value in result.items() if key != 'files'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
