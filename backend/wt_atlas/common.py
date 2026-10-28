"""Deterministic files and content identities shared by offline builders."""
from __future__ import annotations
import hashlib
import json
import os
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
    temp.replace(path)


def write_json(path, value):
    atomic_write(path, canonical(value) + b'\n')


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
