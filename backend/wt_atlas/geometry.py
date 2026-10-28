"""Version-aware GRP decoding and CPU authored-mesh ray intervals.

The supplied decoder understands a specific GRP2 collision-v3 encoding. Any
new format raises an isolated job error. CPU sampling intentionally has its own
geometry-version key; it is not presented as native runtime BLAS parity.
"""
from __future__ import annotations
import importlib.util
import math
from pathlib import Path
import time
import numpy as np
from .common import read_json, write_json, file_hash, content_hash
from .native_binding.grp_directory import parse_grp_directory


def load_decoder(vendor):
    path = Path(vendor) / 'lib/grp_decoder.py'
    spec = importlib.util.spec_from_file_location('wt_supplied_grp_decoder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def decode_grp(path, vendor, profile=None, resource_name=None):
    decoder = load_decoder(vendor)
    raw = Path(path).read_bytes()
    if profile is not None and file_hash(path) != profile['grp_sha256']:
        raise ValueError('GRP hash does not match material/binding profile')
    resources = parse_grp_directory(raw)['entries']
    selected = profile['collision_resource'] if profile is not None else resource_name
    candidates = [entry for entry in resources if entry['class_id'] == 0xACE50000 and (selected is None or entry['name'] == selected)]
    if not candidates:
        raise ValueError('No supported collision resource found')
    results = []
    for entry in candidates:
        body = decoder.collision_body(raw, entry)
        parsed = decoder.parse_collision(body)
        if profile is not None:
            identities = [{key: node[key] for key in ['index', 'name', 'indices_count', 'behavior_flags']} for node in parsed['nodes']]
            if identities != profile['collision_nodes']:
                raise ValueError('Collision node identity differs from material bindings')
            if len(profile['materials']) != len(parsed['nodes']):
                raise ValueError('Material array does not align with collision nodes')
        results.append({'resource': entry, 'parsed': parsed, 'body': body})
    return results


def export_mesh(decoded, output, grp_path):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    reports = []
    for index, item in enumerate(decoded):
        parsed = item['parsed']; vertices, faces, nodes = [], [], []; offset = 0
        for chunk in parsed['chunks']:
            v = np.asarray(chunk['verts'], np.float32); f = np.asarray(chunk['faces'], np.int32)
            vertices.append(v); faces.append(f + offset); nodes.append(np.full(len(f), chunk['node_index'], np.int32)); offset += len(v)
        dest = output / ('collision_' + str(index) + '.npz')
        np.savez_compressed(dest, vertices_m=np.concatenate(vertices) if vertices else np.empty((0, 3)),
                            triangle_indices=np.concatenate(faces) if faces else np.empty((0, 3)),
                            triangle_node=np.concatenate(nodes) if nodes else np.empty(0))
        reports.append({'resource_name': item['resource']['name'], 'file': dest.name, 'sha256': file_hash(dest),
                        'collision_nodes': len(parsed['nodes']), 'mesh_nodes': len(parsed['chunks']),
                        'vertices': sum(len(x) for x in vertices), 'triangles': sum(len(x) for x in faces),
                        'parsed_collision_bytes': parsed['parsed_bytes'], 'collision_body_bytes': parsed['body_bytes'],
                        'nodes': [{k: node[k] for k in ['index', 'name', 'indices_count', 'behavior_flags', 'phys_mat_id']} for node in parsed['nodes']]})
    report = {'grp_sha256': file_hash(grp_path), 'mesh_frame': 'authored source transform; metres',
              'contains_material_bindings': False, 'resources': reports}
    write_json(output / 'decode-report.json', report)
    return report


def make_plan(vendor, profile, config):
    plan = read_json(Path(vendor) / 'data/view.json')
    plan.update(profile.get('view_plan', {}))
    custom = config['compute'].get('view', {})
    plan.update(custom)
    if config['compute'].get('pixel_pitch_m') is not None:
        plan['pixel_pitch_m'] = config['compute']['pixel_pitch_m']
    plan['angles_deg'] = config['compute']['angles_deg']
    pitch = plan['pixel_pitch_m']
    if not isinstance(pitch, (float, int)) or not math.isfinite(pitch) or pitch <= 0:
        raise ValueError('Pixel pitch must be finite and positive')
    if not plan['angles_deg'] or len(set(plan['angles_deg'])) != len(plan['angles_deg']) or any(not math.isfinite(angle) for angle in plan['angles_deg']):
        raise ValueError('Unique finite angles required')
    plan['width'] = math.ceil(plan['world_width_m'] / pitch - config['numerics']['grid_rounding_epsilon'])
    plan['height'] = math.ceil((plan['top_y_m'] - plan['bottom_y_m']) / pitch - config['numerics']['grid_rounding_epsilon'])
    plan['world_width_m'] = plan['width'] * pitch
    plan['bottom_y_m'] = plan['top_y_m'] - plan['height'] * pitch
    return plan


def runtime_meshes(decoded, profile, vendor, config):
    """Use pinned loader vertices when available; authored raw fallback is explicit."""
    if not config['compute'].get('prefer_pinned_runtime_vertices'):
        return decoded['parsed']['chunks'], 'decoded_source_vertices'
    loader_file = Path(vendor) / 'data/runtime/loader.json'
    if not loader_file.exists():
        return decoded['parsed']['chunks'], 'decoded_source_vertices_no_runtime'
    import sys
    library = str((Path(vendor) / 'lib').resolve())
    if library not in sys.path: sys.path.insert(0, library)
    from wt_armor.native_blas_chunk import collision_chunks
    from wt_armor.native_loaded_blas import LoadedChunks
    from wt_armor.native_loaded_ray import chunk_hits
    loaded = LoadedChunks(loader_file)
    if not any(vehicle == profile['vehicle'] for vehicle, node in loaded.rows):
        return decoded['parsed']['chunks'], 'decoded_source_vertices_no_matching_runtime'
    chunks = loaded.apply(profile['vehicle'], collision_chunks(decoded['body'], decoded['parsed']['nodes']))
    result = []
    for chunk in chunks:
        chunk_hits(chunk, config['numerics']['ray_dummy_origin'], config['numerics']['ray_dummy_direction'],
                   0, True, config['numerics']['runtime_min_barycentric_numerator'], config['numerics']['runtime_max_barycentric_sum'])
        vertices = chunk['_original_ray_vertices'][0]
        transform = np.asarray(decoded['parsed']['authored_tm'][chunk['node_index']], np.float64).reshape(4, 3).T
        world = vertices.astype(np.float64) @ transform[:, :3].T + transform[:, 3]
        result.append({'node_index': chunk['node_index'], 'verts': world, 'faces': chunk['faces']})
    return result, 'pinned_runtime_vertices_authored_transform'


def build_cpu_layers(grp_path, profile_path, vendor, output, config, decoded=None):
    started = time.perf_counter()
    profile = read_json(profile_path)
    plan = make_plan(vendor, profile, config)
    dependencies = {'grp': file_hash(grp_path), 'profile': file_hash(profile_path),
                    'decoder': file_hash(Path(vendor) / 'lib/grp_decoder.py'), 'builder': file_hash(__file__),
                    'directory_parser': file_hash(Path(__file__).parent / 'native_binding/grp_directory.py'),
                    'raster': file_hash(Path(__file__).with_name('raster.py')),
                    'compiled_raster': config['compute'].get('compiled_raster', False),
                    'plan': plan, 'numerics': config['numerics'], 'geometry_model': config['scope']['cpu_geometry_version']}
    runtime = Path(vendor) / 'data/runtime'
    dependencies['prefer_pinned_runtime_vertices'] = config['compute'].get('prefer_pinned_runtime_vertices')
    if runtime.exists():
        dependencies['runtime_files'] = {p.name: file_hash(p) for p in sorted(runtime.iterdir()) if p.is_file()}
    fingerprint = content_hash(dependencies)
    output = Path(output) / profile['vehicle'] / fingerprint
    metadata = output / (profile['vehicle'] + '.layers.json')
    packet = metadata.with_suffix('.npz')
    if not config['compute']['rebuild'] and metadata.exists() and packet.exists():
        previous = read_json(metadata)
        if previous.get('request_fingerprint') == fingerprint and previous.get('arrays_sha256') == file_hash(packet):
            return metadata, True
    if decoded is None: decoded = decode_grp(grp_path, vendor, profile)
    parsed = decoded[0]['parsed']; meshes, vertex_source = runtime_meshes(decoded[0], profile, vendor, config); width, height = plan['width'], plan['height']
    center_x, center_z = profile['view_group']['center_xz_m']; half_x, half_z = profile['view_group']['half_xz_m']
    pitch = plan['pixel_pitch_m']; epsilon = config['numerics']['triangle_epsilon']; cluster = config['numerics']['edge_cluster_tolerance_m']
    records = {key: [] for key in ['ray_id', 'node', 'entry_m', 'exit_m', 'incidence_cos', 'extra_entry_m']}
    for ai, angle in enumerate(plan['angles_deg']):
        radians = math.radians(angle); c, s = math.cos(radians), math.sin(radians)
        direction = np.array([-c, 0, -s], np.float64)
        camera_distance = abs(c) * half_x + abs(s) * half_z + plan['camera_margin_m']
        for chunk in meshes:
            vertices = np.asarray(chunk['verts'], np.float64)
            faces = np.asarray(chunk['faces'], np.int32)
            triangle = vertices[faces]
            normals = np.cross(triangle[:, 1] - triangle[:, 0], triangle[:, 2] - triangle[:, 0])
            authored = np.asarray(parsed['authored_tm'][chunk['node_index']], np.float64).reshape(4, 3).T
            # Source inward/outward uses inverse-transformed local normals.
            # Mirrored authored transforms must not reverse the source winding.
            normals *= np.sign(np.linalg.det(authored[:, :3]))
            lengths = np.linalg.norm(normals, axis=1)
            normals = np.divide(normals, lengths[:, None], out=np.zeros_like(normals), where=lengths[:, None] > 0)
            facing = normals @ direction
            vx, vy, vz = vertices[:, 0] - center_x, vertices[:, 1], vertices[:, 2] - center_z
            horizontal = -s * vx + c * vz
            image_x = (plan['world_width_m'] / 2 - horizontal) / pitch - .5
            image_y = (plan['top_y_m'] - vy) / pitch - .5
            depth = camera_distance - (c * vx + s * vz)
            px, py, pz = image_x[faces], image_y[faces], depth[faces]
            if config['compute'].get('compiled_raster', False):
                from .raster import raster_hits, inward_intervals
                ids, distances, face_ids = raster_hits(px, py, pz, facing, width, height, epsilon, plan['ray_limit_m'])
                if len(ids):
                    order = np.lexsort((distances, ids))
                    rays, starts, ends, cosines = inward_intervals(ids[order], distances[order], face_ids[order], facing, cluster)
                    records['ray_id'].extend(rays + ai * width * height)
                    records['node'].extend([chunk['node_index']] * len(rays))
                    records['entry_m'].extend(starts); records['exit_m'].extend(ends)
                    records['incidence_cos'].extend(cosines); records['extra_entry_m'].extend([np.inf] * len(rays))
                continue
            ray_hits, distance_hits, face_hits = [], [], []
            for fi in range(len(faces)):
                x, y, z = px[fi], py[fi], pz[fi]
                if abs(facing[fi]) <= epsilon: continue
                low_x = max(0, math.ceil(float(np.min(x)))); high_x = min(width - 1, math.floor(float(np.max(x))))
                low_y = max(0, math.ceil(float(np.min(y)))); high_y = min(height - 1, math.floor(float(np.max(y))))
                if low_x > high_x or low_y > high_y: continue
                det = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
                if abs(det) <= epsilon: continue
                grid_x, grid_y = np.meshgrid(np.arange(low_x, high_x + 1), np.arange(low_y, high_y + 1))
                a = ((y[1] - y[2]) * (grid_x - x[2]) + (x[2] - x[1]) * (grid_y - y[2])) / det
                b = ((y[2] - y[0]) * (grid_x - x[2]) + (x[0] - x[2]) * (grid_y - y[2])) / det
                cc = 1 - a - b
                valid = (a >= -epsilon) & (b >= -epsilon) & (cc >= -epsilon)
                t = a * z[0] + b * z[1] + cc * z[2]
                valid &= (t >= 0) & (t <= plan['ray_limit_m'])
                if valid.any():
                    ray_hits.append((grid_y[valid] * width + grid_x[valid]).astype(np.int32))
                    distance_hits.append(t[valid]); face_hits.append(np.full(int(valid.sum()), fi, np.int32))
            if not ray_hits: continue
            ids = np.concatenate(ray_hits); distance = np.concatenate(distance_hits); face = np.concatenate(face_hits)
            order = np.lexsort((distance, ids)); ids, distance, face = ids[order], distance[order], face[order]
            boundaries = np.r_[0, np.flatnonzero(ids[1:] != ids[:-1]) + 1, len(ids)]
            for begin, end in zip(boundaries[:-1], boundaries[1:]):
                unique = [int(begin)]
                for position in range(int(begin) + 1, int(end)):
                    if distance[position] - distance[unique[-1]] > cluster: unique.append(position)
                for order_index, position in enumerate(unique):
                    if facing[face[position]] >= 0: continue
                    following = unique[order_index + 1] if order_index + 1 < len(unique) else None
                    exit_ = np.inf if following is None or facing[face[following]] <= 0 else distance[following]
                    records['ray_id'].append(int(ids[position]) + ai * width * height)
                    records['node'].append(chunk['node_index']); records['entry_m'].append(distance[position])
                    records['exit_m'].append(exit_); records['incidence_cos'].append(-facing[face[position]])
                    records['extra_entry_m'].append(np.inf)
    arrays = {key: np.asarray(value, np.int32 if key in ['ray_id', 'node'] else np.float32) for key, value in records.items()}
    output.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(packet, **arrays)
    meta = {'vehicle': profile['vehicle'], 'plan': plan, 'materials': profile['materials'],
            'entries': len(arrays['ray_id']), 'all_inward_node_intervals_retained': True,
            'geometry_model': config['scope']['cpu_geometry_version'],
            'geometry_scope': 'Orthographic pixel centers on decoded authored-pose triangles; not native runtime BLAS parity',
            'vertex_source': vertex_source, 'edge_cluster_tolerance_m': cluster, 'grp_sha256': profile['grp_sha256'],
            'arrays_sha256': file_hash(packet), 'request_fingerprint': fingerprint,
            'profile_provenance': profile['provenance'], 'dependencies': dependencies,
            'build_seconds': time.perf_counter() - started}
    write_json(metadata, meta)
    return metadata, False
