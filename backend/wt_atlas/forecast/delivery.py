"""Conservative use of source-declared missile distance parameters.

The captured minimum and maximum do not establish a flight or activation
model. An impact map outside those declarations remains usable for research,
but cannot by itself establish that a modeled shot reaches its target.
"""
from collections import Counter

from .data import number


def delivery_issue(profile, distance):
    if not number(distance) or distance < 0:
        return 'invalid_physical_distance'
    evidence = profile.get('provenance', {}).get('chemical_energy')
    if evidence is None:
        return None
    if not isinstance(evidence, dict):
        return 'invalid_missile_delivery_source_bounds'
    minimum = evidence.get('source_min_distance_m')
    maximum = evidence.get('source_max_distance_m')
    if any(value is not None and (not number(value) or value < 0) for value in (minimum, maximum)):
        return 'invalid_missile_delivery_source_bounds'
    if minimum is not None and maximum is not None and minimum > maximum:
        return 'invalid_missile_delivery_source_bounds'
    if ((minimum is not None and distance < minimum) or
        (maximum is not None and distance > maximum)):
        return 'delivery_unverified_outside_source_distance_bounds'
    return None


def audit_profiles(profiles, config):
    """List source-bound point profiles; no map, variant or flight check is implied."""
    if not isinstance(profiles, list) or any(not isinstance(p, dict) for p in profiles):
        raise ValueError('Ammunition profile array required')
    names = [p.get('name') for p in profiles]
    if any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError('Unique ammunition profile names required')
    distances = {s['physical_distance_m'] for s in config['scenarios']}
    if not distances or any(not number(d) or d < 0 for d in distances):
        raise ValueError('Valid configured physical distances required')
    rows = []
    for profile in profiles:
        distance = profile.get('distance_m')
        if profile.get('reference_only') or profile.get('mode') not in ('KE', 'CE') or distance not in distances:
            continue
        issue = delivery_issue(profile, distance)
        if not issue: continue
        provenance = profile.get('provenance', {}); evidence = provenance.get('chemical_energy')
        owners = provenance.get('penetration_source_ammo_ids', [])
        if not isinstance(owners, list) or any(not isinstance(x, str) or '::' not in x for x in owners):
            raise ValueError('Invalid source ownership identities')
        evidence = evidence if isinstance(evidence, dict) else {}
        rows.append({'profile_id': profile['name'], 'distance_m': distance, 'ammo_ids': sorted(owners),
                     'source_min_distance_m': evidence.get('source_min_distance_m'),
                     'source_max_distance_m': evidence.get('source_max_distance_m'), 'reason': issue})
    rows.sort(key=lambda x: (x['distance_m'], x['profile_id']))
    owners = {owner for row in rows for owner in row['ammo_ids']}
    return {'affected_profiles': len(rows), 'distinct_ammo_ids': len(owners),
            'distinct_vehicles': len({owner.split('::', 1)[0] for owner in owners}),
            'profiles_by_distance_m': {str(k): v for k, v in sorted(Counter(r['distance_m'] for r in rows).items())},
            'rows': rows}
