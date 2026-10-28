"""Atomic result publication kept separate from the scientific cache identity."""
from __future__ import annotations

from pathlib import Path
import tempfile

from .common import file_hash, read_json, safe_id


def recalculate_layers_atomic(metadata, ammo_path, output, config, ray_ids=None):
    """Compute in a sibling directory and replace each completed NPZ atomically.

    Export staging may hard-link previous results. Replacing an inode leaves
    those verified files unchanged, including when a computation is interrupted.
    Keep this I/O wrapper outside cpu_model.py so storage refactors do not
    invalidate the fingerprint of an unchanged physical model.
    """
    from .cpu_model import recalculate_layers

    safe_id(read_json(metadata)['vehicle'])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.wt-atlas-compute-', dir=output.parent) as directory:
        staging = Path(directory)
        report = recalculate_layers(metadata, ammo_path, staging, config, ray_ids)
        paths = []
        for result in report['results']:
            name = result['file']
            if Path(name).name != name or not name.endswith('.npz'):
                raise ValueError('Expected a local NPZ result filename')
            source = staging / name
            if file_hash(source) != result['sha256']:
                raise ValueError('Computed NPZ changed before publication: ' + name)
            paths.append((source, output / name))
        for source, destination in paths:
            source.replace(destination)
        (staging / 'recalculation-report.json').replace(output / 'recalculation-report.json')
    return report
