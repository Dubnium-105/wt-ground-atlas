"""Plan isolated exact-distance missile research against verified ground geometry."""
import re

from .forecast.data import br_step, number
from .kinetic_missile_points import ADAPTER, DISTANCES


def plan_targets(catalog, config, bindings, profiles):
    vehicles = {v['id']: v for v in catalog['vehicles']}
    if len(vehicles) != len(catalog['vehicles']): raise ValueError('Duplicate vehicle identity')
    if (len(profiles) != len(DISTANCES) or {p['distance_m'] for p in profiles} != set(DISTANCES) or
        any(p.get('reference_only') is not True or p.get('provenance', {}).get('adapter') != ADAPTER for p in profiles)):
        raise ValueError('Four exact reference-only research points required')
    owners = {tuple(p['provenance'].get('penetration_source_ammo_ids', [])) for p in profiles}
    versions = {(p['provenance'].get('client_version'), p['provenance'].get('datamine_commit')) for p in profiles}
    if len(owners) != 1 or len(versions) != 1: raise ValueError('Mixed source identity')
    owner_ids = next(iter(owners))
    if len(owner_ids) != 1 or '::' not in owner_ids[0]: raise ValueError('Exact owner required')
    owner = vehicles.get(owner_ids[0].split('::', 1)[0])
    if owner is None or owner.get('kind') != 'ground' or not any(a.get('id') == owner_ids[0] for a in owner.get('ammo', [])):
        raise ValueError('Owned ground projectile required')
    owner_br = owner.get('br', {}).get('current')
    if not number(owner_br): raise ValueError('Owner base BR missing')
    scenarios = config['scenarios']
    pairs = {(s['physical_distance_m'], s['yaw_deg']) for s in scenarios}
    distances = {x[0] for x in pairs}; yaws = {x[1] for x in pairs}
    if distances != set(DISTANCES) or len(yaws) != 24 or pairs != {(d, y) for d in distances for y in yaws}:
        raise ValueError('Four distances and all 24 yaw scenarios required')
    version, commit = next(iter(versions)); result = []
    for vehicle in vehicles.values():
        br = vehicle.get('br', {}).get('current')
        if (vehicle.get('kind') != 'ground' or vehicle.get('system_role') == 'component' or
            vehicle.get('nation') == owner.get('nation') or not number(br) or
            abs(br_step(br) - br_step(owner_br)) > config['matchmaking']['spread_steps']):
            continue
        identifier = vehicle['id']
        if not re.fullmatch(r'[a-z0-9_]+', identifier): raise ValueError('Unsafe target identity')
        bound = bindings.get(identifier)
        identity = (bound or {}).get('identity', {})
        if (not bound or not bound.get('target_sha256') or not bound.get('source_job_sha256') or
            identity.get('vehicle_id') != identifier or identity.get('client_version') != version or
            identity.get('datamine_commit') != commit or not yaws <= set(identity.get('plan', {}).get('angles_deg', []))):
            raise ValueError('Exact 24-yaw target geometry unavailable: ' + identifier)
        result.append({'vehicle_id': identifier, 'target_sha256': bound['target_sha256'],
                       'source_job_sha256': bound['source_job_sha256'], 'angles_deg': sorted(yaws)})
    if not result: raise ValueError('No matching targets with source geometry')
    return {'owner_id': owner['id'], 'ammo_id': owner_ids[0], 'source_version': version, 'datamine_commit': commit,
            'target_count': len(result), 'planned_maps': len(result) * len(DISTANCES),
            'angles_per_map': len(yaws), 'targets': sorted(result, key=lambda x: x['vehicle_id']),
            'scope': 'Base catalog BR and nation matching only; research impact maps, not dynamic loadout eligibility or formal ranking.',
            'reference_only': True, 'publishable': False, 'formal_rankings': 0}
