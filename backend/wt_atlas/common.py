"""Deterministic files and content identities shared by offline builders."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def content_hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path, chunk_bytes=1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(chunk_bytes), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.part')
    with temp.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    # Windows readers briefly deny delete-sharing during an ordinary read.
    # Keep the old destination intact and retry only the atomic replacement;
    # never fall back to truncating a visible progress or publication file.
    for attempt in range(6):
        try:
            temp.replace(path)
            break
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                raise
            time.sleep(.01 * 2**attempt)


def write_json(path, value):
    atomic_write(path, canonical(value) + b'\n')


def stage_verified_file(source, destination, expected_sha256, *, allow_hardlink=True):
    """Stage an immutable result without duplicating it on the same filesystem.

    A hard link shares disk blocks with the content-addressed computation cache.
    Filesystems that cannot link (including different volumes) fall back to an
    atomic copy. Neither a corrupt input nor an interrupted copy replaces the
    previous staging file. Callers must replace, never edit, cached results.
    """
    source, destination = Path(source), Path(destination)
    if file_hash(source) != expected_sha256:
        raise ValueError('Computed file hash changed before staging: ' + str(source))
    if destination.is_file() and file_hash(destination) == expected_sha256:
        return 'reused'
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + destination.name + '-', suffix='.part', dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    temporary.unlink()
    try:
        try:
            if not allow_hardlink:
                raise OSError('This producer does not replace result files atomically')
            os.link(source, temporary)
            method = 'linked'
        except OSError:
            shutil.copyfile(source, temporary)
            method = 'copied'
        if file_hash(temporary) != expected_sha256:
            raise ValueError('Computed file changed while staging: ' + str(source))
        temporary.replace(destination)
        return method
    finally:
        temporary.unlink(missing_ok=True)


def load_config(path):
    path = Path(path).resolve()
    config = read_json(path)
    root = path.parent.parent
    return config, root


def resolved(root, value):
    p = Path(value).expanduser()
    return p.resolve() if p.is_absolute() else (Path(root) / p).resolve()


def safe_id(value):
    if not isinstance(value, str) or not value or any(not (c.isascii() and (c.isalnum() or c in '_-')) for c in value):
        raise ValueError('Expected a file-safe stable ID')
    return value
