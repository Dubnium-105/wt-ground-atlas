"""Atomic, resumable offline storage for explicitly non-publishable maps."""
import os
from pathlib import Path
import tempfile

import numpy as np
from .common import content_hash, file_hash, read_json, write_json
from .forecast.inventory import verify_array

MARKER = '.wt-research-maps'


def open_run(output, identity):
    output = Path(output)
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError('Symlink research output')
    marker = output / MARKER
    if marker.is_symlink() or (output / 'arrays').is_symlink():
        raise ValueError('Symlink research output member')
    if output.exists() and any(output.iterdir()) and not marker.is_file():
        raise ValueError('Unmanaged research output')
    if marker.exists() and read_json(marker) != identity:
        raise ValueError('Research output source/model identity changed')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'arrays').mkdir(exist_ok=True)
    if not marker.exists(): write_json(marker, identity)
    return output


def save_map(output, identity, ammo, actual, plan, *, budget=512 * 1024**2):
    if ammo.get('reference_only') is not True:
        raise ValueError('Research maps require reference-only ammunition')
    output = open_run(output, identity)
    profile_hash = content_hash(ammo)
    map_id = content_hash({'run': identity, 'profile_sha256': profile_hash})
    array = output / 'arrays' / (map_id + '.npz')
    metadata = array.with_suffix('.json')
    if array.is_symlink() or metadata.is_symlink(): raise ValueError('Symlink research map')
    common = {'map_id': map_id, 'ammo_id': ammo['name'], 'ammo_profile_sha256': profile_hash,
              'target_id': identity['target_id'], 'target_sha256': identity['target_sha256'],
              'source_version': ammo['provenance']['client_version'],
              'datamine_commit': ammo['provenance']['datamine_commit'],
              'physical_distance_m': ammo['distance_m'], 'array_file': 'arrays/' + array.name,
              'reference_only': True, 'publishable': False}
    if array.is_file() and metadata.is_file():
        previous = read_json(metadata)
        if all(previous.get(k) == v for k, v in common.items()) and verify_array((str(output), previous, budget))['status'] == 'verified':
            return previous, True
    probability, charge, status, flags = actual
    fd, temporary = tempfile.mkstemp(prefix=map_id + '.', suffix='.part', dir=array.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            np.savez_compressed(stream, penetration_probability=probability, model_required_charge_mm=charge,
                status=status, material_response_flags=flags, angles_deg=np.asarray(plan['angles_deg'], np.float64),
                **{k: np.float64(plan[k]) for k in ('pixel_pitch_m', 'world_width_m', 'top_y_m', 'bottom_y_m')})
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, array)
    finally:
        if Path(temporary).exists(): Path(temporary).unlink()
    saved = {**common, 'array_sha256': file_hash(array),
             'status_counts': {str(i): int(np.count_nonzero(status == i)) for i in range(4)}}
    check = verify_array((str(output), saved, budget))
    if check['status'] != 'verified': raise ValueError('Research map validation failed: ' + str(check))
    write_json(metadata, saved)
    return saved, False
