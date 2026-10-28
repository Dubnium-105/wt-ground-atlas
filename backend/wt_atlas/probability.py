"""Validate and publish probability arrays without altering their meaning.

No GRP, NPZ, ray layers or model charge is included in the public export. Each
requested view contains quantized probabilities and the original categorical
state / material flags. Unknowns have a dedicated code, separate from real 0.
"""
from __future__ import annotations
import gzip
import hashlib
import shutil
import time
from pathlib import Path
import numpy as np
from .common import read_json, write_json, content_hash, file_hash, atomic_write, safe_id


def physical_cache_key(identity):
    """BR is deliberately absent: environment edits do not invalidate physics."""
    required = ('target_id', 'configuration_id', 'ammo_id', 'ammo_profile_sha256',
                'physical_distance_m', 'geometry_version', 'configuration_version',
                'model_version', 'source_version', 'angles_deg', 'width', 'height', 'pixel_pitch_m')
    missing = [key for key in required if key not in identity]
    if missing:
        raise ValueError('Missing physical identity fields: ' + ', '.join(missing))
    return content_hash({key: identity[key] for key in required})


def validate_arrays(probability, status, flags, plan):
    if probability.ndim != 3 or probability.shape != status.shape or flags.shape != status.shape:
        raise ValueError('Expected equally shaped [angle, image_y, image_x] arrays')
    angles = plan['angles_deg']
    if len(set(angles)) != len(angles) or len(angles) != probability.shape[0]:
        raise ValueError('Angle order/count mismatch')
    if plan['width'] != probability.shape[2] or plan['height'] != probability.shape[1]:
        raise ValueError('Metadata grid dimensions mismatch')
    if not np.isfinite(plan['pixel_pitch_m']) or plan['pixel_pitch_m'] <= 0:
        raise ValueError('Pixel pitch must be positive and finite')
    if not np.isin(status, [0, 1, 2, 3]).all():
        raise ValueError('Unrecognized status code')
    finite = np.isfinite(probability)
    if np.any(finite & ((probability < 0) | (probability > 1))):
        raise ValueError('Probability outside [0, 1]')
    if np.any((status == 3) & ~finite):
        raise ValueError('Computed status must have a finite probability')
    if np.any((status != 3) & finite):
        raise ValueError('Non-computed status must preserve NaN; no implicit zero filling')


def encode_view(probability, status, flags, quantization_max, unknown_value):
    if not (0 < quantization_max < unknown_value <= np.iinfo(np.uint16).max):
        raise ValueError('Invalid quantization range / unknown sentinel')
    q = np.full(probability.shape, unknown_value, dtype='<u2')
    valid = (status == 3) & np.isfinite(probability)
    q[valid] = np.rint(probability[valid].astype(np.float64) * quantization_max).astype('<u2')
    errors = np.abs(q[valid].astype(np.float64) / quantization_max - probability[valid])
    packet = q.tobytes(order='C') + np.asarray(status, np.uint8).tobytes(order='C') + np.asarray(flags, np.uint8).tobytes(order='C')
    return packet, float(errors.max()) if errors.size else 0.0, float(errors.sum()), int(errors.size)


def decode_view(packet, width, height, quantization_max, unknown_value):
    n = width * height
    if len(packet) != n * (np.dtype('<u2').itemsize + np.dtype('u1').itemsize * 2):
        raise ValueError('Invalid probability shard length')
    q = np.frombuffer(packet, '<u2', count=n)
    status = np.frombuffer(packet, 'u1', offset=n * np.dtype('<u2').itemsize, count=n)
    flags = np.frombuffer(packet, 'u1', offset=n * (np.dtype('<u2').itemsize + np.dtype('u1').itemsize), count=n)
    p = np.where(q == unknown_value, np.nan, q.astype(np.float64) / quantization_max)
    return p.reshape(height, width), status.reshape(height, width), flags.reshape(height, width)


def _metadata_for_array(path, input_root, plan, summary, ammo_profiles, config, source_sha256=None):
    target_id, ammo_id = path.stem.rsplit('.', 1)
    safe_id(target_id); safe_id(ammo_id)
    ammo = next((item for item in ammo_profiles if item.get('name') == ammo_id), None)
    if ammo is None:
        raise ValueError('No exact ammunition profile: ' + ammo_id)
    layers_path = input_root / 'geometry_layers' / (target_id + '.layers.json')
    layer = read_json(layers_path) if layers_path.exists() else {}
    source_group = next((g for g in summary.get('groups', []) if g.get('vehicle') == target_id), {})
    source_version = summary.get('client_version')
    # Neither physical distance nor version is inferred from the vehicle's BR.
    identity = {
        'target_id': target_id, 'configuration_id': target_id,
        'ammo_id': ammo_id, 'mode': ammo['mode'],
        'ammo_profile_sha256': content_hash(ammo),
        'physical_distance_m': ammo.get('distance_m'),
        'geometry_version': layer.get('grp_sha256') or source_group.get('grp_sha256'),
        'configuration_version': layer.get('dependencies', {}).get('profile') or (file_hash(layers_path) if layers_path.exists() else content_hash(plan)),
        'model_version': summary.get('algorithm', config['scope']['model_version']),
        'source_version': source_version,
        'datamine_commit': summary.get('datamine_commit'),
        'angles_deg': list(plan['angles_deg']), 'width': int(plan['width']), 'height': int(plan['height']),
        'pixel_pitch_m': float(plan['pixel_pitch_m']),
        'world_width_m': plan.get('world_width_m'), 'top_y_m': plan.get('top_y_m'), 'bottom_y_m': plan.get('bottom_y_m'),
        'coordinate_convention': plan.get('rotation_convention'),
        'source_npz_sha256': source_sha256 or file_hash(path),
        'layer_npz_sha256': source_group.get('layer_arrays_sha256') or layer.get('arrays_sha256'),
        'reference_ammunition_only': True,
        'proves_vehicle_ammunition_availability': False,
        'requires_source_version_match_for_pair_use': True,
        'conditions': 'Pristine authored pose, straight path; conditional protected-inner-target penetration, not kill probability',
        'model_required_charge_is_rhae': False,
    }
    if identity['geometry_version'] is None:
        raise ValueError('Geometry provenance is required')
    identity['id'] = physical_cache_key(identity)
    identity['key'] = target_id + '.' + ammo_id
    return identity


def export_atlas(input_root, output_root, config, array_files=None, metadata_root=None):
    """Export every supplied map/angle, validating source hashes and metadata."""
    started = time.perf_counter()
    input_root, output_root = Path(input_root).resolve(), Path(output_root).resolve()
    metadata_root = Path(metadata_root) if metadata_root else output_root
    output_root.mkdir(parents=True, exist_ok=True)
    plan = read_json(input_root / 'provenance/plan.json')
    summary = read_json(input_root / 'summary.json')
    ammo_profiles = read_json(input_root / 'provenance/ammunition.json')
    source_format = read_json(input_root / 'format.json')
    scope = read_json(input_root / 'provenance/model_scope.json')
    exp = config['export']
    index = {'schema_version': 1, 'kind': 'conditional-probability-atlas',
             'encoding': {'probability_dtype': 'uint16-le', 'quantization_max': exp['quantization_max'],
                          'unknown_value': exp['unknown_value'], 'layout': ['probability', 'status', 'material_response_flags'],
                          'order': 'row-major image_y,image_x', 'status_dtype': 'uint8', 'flags_dtype': 'uint8'},
             'status_legend': source_format['per_ammunition_arrays']['status']['values'],
             'flag_legend': config['material_response_flags'], 'scope': scope, 'entries': [],
             'source_created_at': summary.get('created_at_utc'),
             'source_version': summary.get('client_version'), 'datamine_commit': summary.get('datamine_commit'),
             'compression': {'format': 'gzip-sidecar', 'level': exp['gzip_level']},
             'reference_ammunition_is_not_availability': True,
             'default_precision': 'conditional_unvalidated', 'renderer_does_not_load_raw_npz': True}
    max_error, total_error, valid_count, raw_bytes, gz_bytes, reused, reused_gzip = 0.0, 0.0, 0, 0, 0, 0, 0
    previous_shards = {}
    try:
        previous_index = read_json(metadata_root / 'index.json')
        if previous_index.get('compression') == index['compression']:
            previous_shards = {shard['url']: shard for entry in previous_index['entries'] for shard in entry['shards']}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    expected = {}
    for group in summary.get('groups', []):
        for item in group.get('groups', []):
            key = item['array_file']
            if key in expected:
                raise ValueError('Duplicate source array in summary: ' + key)
            expected[key] = item.get('array_sha256')
    seen_entries = set()
    for path in sorted(map(Path, array_files) if array_files is not None else (input_root / 'arrays').glob('*.npz')):
        path = path.resolve()
        key = path.relative_to(input_root).as_posix()
        declared_hash = expected.get(key)
        if not isinstance(declared_hash, str) or len(declared_hash) != 64 or any(c not in '0123456789abcdef' for c in declared_hash):
            raise ValueError('Missing or invalid source array SHA256: ' + key)
        if file_hash(path) != declared_hash:
            raise ValueError('Source array hash mismatch: ' + key)
        with np.load(path, allow_pickle=False) as packet:
            if 'penetration_probability' not in packet:
                continue
            p, status, flags = (packet[name] for name in ['penetration_probability', 'status', 'material_response_flags'])
            local_plan = dict(plan)
            if 'angles_deg' in packet:
                for name in ['angles_deg', 'pixel_pitch_m', 'world_width_m', 'top_y_m', 'bottom_y_m']:
                    if name in packet:
                        local_plan[name] = packet[name].tolist()
                local_plan['height'], local_plan['width'] = p.shape[1:]
            validate_arrays(p, status, flags, local_plan)
            meta = _metadata_for_array(path, input_root, local_plan, summary, ammo_profiles, config, declared_hash)
            if meta['id'] in seen_entries:
                raise ValueError('Duplicate probability entry: ' + meta['key'])
            seen_entries.add(meta['id'])
            meta['shards'] = []
            for ai, angle in enumerate(meta['angles_deg']):
                blob, error, errsum, n = encode_view(p[ai], status[ai], flags[ai], exp['quantization_max'], exp['unknown_value'])
                digest = hashlib.sha256(blob).hexdigest()
                rel = Path('shards') / meta['target_id'] / meta['ammo_id'] / (digest + '.bin')
                dest = output_root / rel
                if dest.exists() and file_hash(dest) == digest:
                    reused += 1
                else:
                    atomic_write(dest, blob)
                compressed_path = dest.with_suffix(dest.suffix + '.gz')
                previous = previous_shards.get(rel.as_posix(), {})
                if (previous.get('sha256') == digest and previous.get('gzip_sha256')
                        and compressed_path.is_file()
                        and compressed_path.stat().st_size == previous.get('gzip_bytes')
                        and file_hash(compressed_path) == previous['gzip_sha256']):
                    compressed_size = previous['gzip_bytes']
                    compressed_digest = previous['gzip_sha256']
                    reused_gzip += 1
                else:
                    compressed = gzip.compress(blob, compresslevel=exp['gzip_level'], mtime=0)
                    atomic_write(compressed_path, compressed)
                    compressed_size = len(compressed)
                    compressed_digest = hashlib.sha256(compressed).hexdigest()
                state_counts = {str(code): int(count) for code, count in zip(*np.unique(status[ai], return_counts=True))}
                finite = (status[ai] == 3) & np.isfinite(p[ai])
                stat = {'pixels': int(status[ai].size), 'status_counts': state_counts,
                        'known_mean': float(np.mean(p[ai][finite], dtype=np.float64)) if finite.any() else None,
                        'known_mean_is_not_shot_probability': True,
                        'quantization_max_abs_error': error}
                item = {'angle_index': ai, 'angle_deg': angle, 'url': rel.as_posix(), 'sha256': digest,
                        'bytes': len(blob), 'gzip_bytes': compressed_size, 'gzip_url': rel.as_posix() + '.gz',
                        'gzip_sha256': compressed_digest, 'summary': stat}
                preview = input_root / 'images/native' / meta['key'] / ('yaw_' + f'{angle:03g}' + '.png')
                if exp['include_native_preview'] and preview.is_file():
                    preview_rel = rel.with_suffix('.png')
                    target = output_root / preview_rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(preview, target)
                    item['preview_url'] = preview_rel.as_posix()
                meta['shards'].append(item)
                previous_shards[rel.as_posix()] = item
                max_error = max(max_error, error); total_error += errsum; valid_count += n
                raw_bytes += len(blob); gz_bytes += compressed_size
            index['entries'].append(meta)
    if not index['entries']:
        raise ValueError('No supported probability arrays')
    if max_error > exp['max_export_error']:
        raise ValueError('Quantization budget exceeded')
    index['content_hash'] = content_hash(index)
    write_json(metadata_root / 'index.json', index)
    atomic_write(metadata_root / 'index.json.gz', gzip.compress((metadata_root / 'index.json').read_bytes(), compresslevel=exp['gzip_level'], mtime=0))
    report = {'schema_version': 1, 'input_groups': len(index['entries']),
              'targets': len({entry['target_id'] for entry in index['entries']}),
              'views': sum(len(entry['shards']) for entry in index['entries']),
              'quantization_max_abs_error': max_error,
              'quantization_mean_abs_error': total_error / valid_count if valid_count else None,
              'valid_samples': valid_count, 'uncompressed_shard_bytes': raw_bytes,
              'gzip_shard_bytes': gz_bytes, 'index_bytes': (metadata_root / 'index.json').stat().st_size,
              'index_gzip_bytes': (metadata_root / 'index.json.gz').stat().st_size,
              'reused_shards': reused, 'reused_gzip_shards': reused_gzip, 'build_seconds': time.perf_counter() - started,
              'raw_geometry_published': False, 'raw_npz_published': False,
              'unknown_equals_zero': False, 'physical_recalculation_performed': False,
              'source_array_hashes_verified': True, 'content_hash': index['content_hash']}
    write_json(metadata_root / 'export-report.json', report)
    return report
