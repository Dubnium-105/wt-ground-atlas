#!/usr/bin/env python3
"""Verify missile delivery gating using independently indexed real source maps."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import file_hash, read_json, write_json
from wt_atlas.forecast.data import Atlas, digest
from wt_atlas.forecast.delivery import delivery_issue
from wt_atlas.forecast.engine import Forecast, ShotCache
from wt_atlas.forecast.inventory import verify_array


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('catalog', 'ammunition', 'config', 'bindings', 'index', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('attacker', 'target', 'ammo-id'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--outside-distance', type=float, required=True)
    parser.add_argument('--inside-distance', type=float, required=True)
    args = parser.parse_args()
    inputs = {name: getattr(args, name.replace('-', '_')) for name in ('catalog', 'ammunition', 'config', 'bindings', 'index')}
    if args.out.resolve() in {path.resolve() for path in inputs.values()}: raise ValueError('Output overlaps source')
    hashes = {name: file_hash(path) for name, path in inputs.items()}
    wal = Path(str(args.index) + '-wal')
    def wal_hash(): return file_hash(wal) if wal.exists() and wal.stat().st_size else None
    source_wal_hash = wal_hash()
    code_paths = {'verification': Path(__file__), 'delivery': ROOT / 'backend/wt_atlas/forecast/delivery.py',
                  'engine': ROOT / 'backend/wt_atlas/forecast/engine.py'}
    code_hashes = {name: file_hash(path) for name, path in code_paths.items()}
    catalog = read_json(args.catalog); config = read_json(args.config); bindings = read_json(args.bindings)
    if bindings.get('status') != 'completed' or bindings.get('errors'):
        raise ValueError('Independent geometry receipt is incomplete')
    target_binding = bindings['bindings'].get(args.target)
    if not target_binding: raise ValueError('Target lacks independent geometry')
    original_vehicles = catalog['vehicles']; catalog = {**catalog, 'vehicles': [v for v in original_vehicles if v['id'] in (args.attacker, args.target)]}
    if {v['id'] for v in catalog['vehicles']} != {args.attacker, args.target}:
        raise ValueError('Attacker and target identities are required')
    if args.attacker == args.target or not args.ammo_id.startswith(args.attacker + '::'):
        raise ValueError('Exact attacker ammunition ownership required')
    db = sqlite3.connect(args.index.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('BEGIN')  # Read root and selected rows from one SQLite snapshot.
        root_row = db.execute("SELECT value FROM settings WHERE key='root'").fetchone()
        if not root_row: raise ValueError('Indexed atlas root is missing')
        atlas_root = Path(root_row[0])
        rows = db.execute("SELECT target,ammo,distance,id,metadata FROM maps WHERE selected=1 AND status='verified' AND target=?", (args.target,)).fetchall()
    finally: db.close()
    if args.out.resolve().is_relative_to(atlas_root.resolve()): raise ValueError('Output overlaps source atlas')
    class ReadOnlyIndex:
        metadata = {row[3]: json.loads(row[4]) for row in rows}
        lookup = {(row[0], row[1], row[2]): row[3] for row in rows}
    index = ReadOnlyIndex()
    if len(index.metadata) != len(rows) or len(index.lookup) != len(rows): raise ValueError('Ambiguous indexed maps')
    atlas = Atlas(atlas_root, args.ammunition, catalog, index=index)
    with tempfile.TemporaryDirectory(prefix='wt-delivery-real-check-') as temporary:
        cache = ShotCache(Path(temporary) / 'cache.sqlite')
        try:
            engine = Forecast(catalog, atlas, config, cache)
            variants = [v for v in engine.variants if v['vehicle_id'] == args.attacker and v.get('ammo_id') == args.ammo_id]
            if len(variants) != 1: raise ValueError('Exact attacker variant is ambiguous or unavailable')
            attacker = variants[0]; target = {'vehicle_id': args.target,
                'vehicle': next(v for v in catalog['vehicles'] if v['id'] == args.target)}
            profile_by_distance = {p['distance_m']: p for p in attacker['profiles']}
            expected = [args.outside_distance, args.inside_distance]
            if len(set(expected)) != 2 or any(distance not in profile_by_distance for distance in expected):
                raise ValueError('Two exact native distance profiles required')
            points = {}
            for distance in expected:
                profile = profile_by_distance[distance]
                map_id = atlas.map_for(args.target, profile['name'], distance)
                if map_id is None: raise ValueError('Exact indexed map missing')
                meta = atlas.meta[map_id]
                if (meta.get('target_binding') != 'independent_geometry_verified' or
                    meta.get('target_sha256') != target_binding['target_sha256'] or
                    meta.get('ammo_profile_sha256') != digest(profile) or
                    (meta.get('source_version'), meta.get('datamine_commit')) !=
                    (profile['provenance']['client_version'], profile['provenance']['datamine_commit'])):
                    raise ValueError('Exact source profile or target binding mismatch')
                array_check = verify_array((str(atlas_root), meta, 512 * 1024**2))
                if array_check['status'] != 'verified': raise ValueError('Independent array validation failed')
                matches = [s for s in config['scenarios'] if s['physical_distance_m'] == distance and s['yaw_deg'] == 0]
                if len(matches) != 1: raise ValueError('Exact zero-yaw scenario required')
                points[distance] = (profile, meta, matches[0])
            outside, outside_meta, outside_scenario = points[args.outside_distance]
            inside, inside_meta, inside_scenario = points[args.inside_distance]
            identity = target_binding.get('identity', {})
            if (identity.get('vehicle_id') != args.target or identity.get('client_version') != outside_meta['source_version'] or
                identity.get('datamine_commit') != outside_meta['datamine_commit']):
                raise ValueError('Independent geometry version identity mismatch')
            issue = delivery_issue(outside, args.outside_distance)
            if issue != 'delivery_unverified_outside_source_distance_bounds' or delivery_issue(inside, args.inside_distance):
                raise ValueError('Source-declared bounds do not distinguish the requested distances')
            if engine.shot(attacker, target, outside_scenario, 0.) is not None:
                raise ValueError('Outside-source delivery unexpectedly treated as a modeled shot')
            if atlas.loads != 0: raise ValueError('Outside-source shot unexpectedly loaded the map')
            inside_result = engine.shot(attacker, target, inside_scenario, 0.)
            if inside_result is None or inside_result['map_id'] != inside_meta['map_id'] or atlas.loads != 1:
                raise ValueError('In-range conditional impact map was not evaluated')
            hits = cache.hits
            if engine.shot(attacker, target, inside_scenario, 0.) is None or cache.hits <= hits:
                raise ValueError('Valid conditional shot cache was not reusable')
        finally: cache.close()
    if (any(file_hash(path) != hashes[name] for name, path in inputs.items()) or wal_hash() != source_wal_hash or
        any(file_hash(path) != code_hashes[name] for name, path in code_paths.items())):
        raise ValueError('Source changed during verification')
    result = {'schema_version': '1.0.0', 'status': 'passed', 'input_sha256': hashes,
        'index_wal_sha256': source_wal_hash,
        'attacker_id': args.attacker, 'target_id': args.target, 'ammo_id': args.ammo_id,
        'source_version': outside['provenance']['client_version'],
        'datamine_commit': outside['provenance']['datamine_commit'],
        'source_min_distance_m': outside['provenance']['chemical_energy']['source_min_distance_m'],
        'source_max_distance_m': outside['provenance']['chemical_energy']['source_max_distance_m'],
        'outside': {'distance_m': args.outside_distance, 'reason': issue, 'map_id': outside_meta['map_id'],
                    'array_sha256': outside_meta['array_sha256'], 'array_validated': True, 'shot_result': 'unknown'},
        'inside': {'distance_m': args.inside_distance, 'map_id': inside_meta['map_id'],
                   'array_sha256': inside_meta['array_sha256'], 'array_validated': True,
                   'shot_result': 'conditional_impact_only', 'warm_cache_reused': True},
        'code_sha256': {**code_hashes, 'engine_lf': hashlib.sha256(code_paths['engine'].read_bytes().replace(b'\r\n', b'\n')).hexdigest()},
        'scope': 'Real verified indexed geometry and source profiles; no flight, hit, win, kill or damage validation.',
        'formal_rankings': 0, 'publishable': False}
    write_json(args.out, result)
    print(json.dumps({'status': result['status'], 'outside': result['outside']['shot_result'],
                      'inside': result['inside']['shot_result'], 'warm_cache_reused': True}))


if __name__ == '__main__': main()
