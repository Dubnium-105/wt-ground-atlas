"""Bound declared NPY shapes before NumPy allocates arrays from an NPZ."""
import math
from pathlib import Path
import zipfile

import numpy as np


def load_npz_bounded(path: Path, keys, memory_budget: int) -> dict:
    if not isinstance(memory_budget, int) or isinstance(memory_budget, bool) or memory_budget <= 0:
        raise ValueError('Invalid NPZ memory budget')
    keys = tuple(keys)
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate requested NPZ array')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(names) != len(set(names)) or sum(item.file_size for item in entries) > memory_budget:
            raise ValueError('NPZ duplicate member or expanded memory budget exceeded')
        for key in keys:
            try:
                info = archive.getinfo(key + '.npy')
            except KeyError as exc:
                raise ValueError('Missing NPZ array: ' + key) from exc
            with archive.open(info) as stream:
                version = np.lib.format.read_magic(stream)
                if version == (1, 0):
                    shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
                elif version == (2, 0):
                    shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
                else:
                    raise ValueError('Unsupported NPY format version')
                if dtype.hasobject or any(not isinstance(n, int) or n < 0 for n in shape):
                    raise ValueError('Invalid NPZ shape or object dtype')
                payload_bytes = math.prod(shape) * dtype.itemsize
                if payload_bytes > memory_budget or stream.tell() + payload_bytes != info.file_size:
                    raise ValueError('NPY declared shape exceeds payload or memory budget')
    with np.load(path, allow_pickle=False) as packet:
        return {key: packet[key] for key in keys}
