"""Exact source-projectile reuse without changing physical profile ownership.

A belt is a catalog selection with ordered references to immutable homogeneous
profiles. Its components are not independently selectable rounds. Derived range
profiles must name the complete original profile hash, never a similar caliber.
"""
from .data import digest, number


def projectile_groups(ammo, profiles):
    belt = (ammo or {}).get('ordered_belt')
    if belt is None:
        return (tuple(profiles),)
    by_name = {p['name']: p for p in profiles}
    groups = []
    for ref in belt['projectiles']:
        base = by_name.get(ref['profile_name'])
        if base is None or digest(base) != ref['profile_sha256']:
            raise ValueError('Ordered belt component profile hash mismatch')
        group = [base]
        for p in profiles:
            if p is base: continue
            if p.get('provenance', {}).get('range_rebuild', {}).get('source_profile_sha256') == ref['profile_sha256']:
                group.append(p)
        if len({p.get('distance_m') for p in group}) != len(group):
            raise ValueError('Ambiguous ordered belt component distance')
        for p in group:
            provenance = p.get('provenance', {})
            proofs = provenance.get('recovery_bindings', {}).values()
            if (p.get('mode') not in ('KE', 'CE') or p.get('reference_only') or
                    not number(p.get('distance_m')) or p['distance_m'] < 0 or
                    provenance.get('client_version') != belt['source_version'] or
                    provenance.get('datamine_commit') != belt['datamine_commit'] or
                    not proofs or any(r.get('projectile_sha256') != ref['projectile_sha256'] for r in proofs)):
                raise ValueError('Ordered belt component source identity mismatch')
        groups.append(tuple(sorted(group, key=lambda p:p['distance_m'])))
    return tuple(groups)


def ordered_bindings(vehicle, profiles):
    """Validate the imported source receipt before exposing component maps."""
    result = []
    for ammo in vehicle.get('ammo', []):
        belt = ammo.get('ordered_belt')
        if belt is None: continue
        if (belt.get('schema_version') != '1.0.0' or belt.get('kind') != 'exact_source_ordered_belt' or
                belt.get('ammo_id') != ammo['id'] or belt.get('vehicle_id') != vehicle['id'] or
                belt.get('source_version') != vehicle.get('source_attributes', {}).get('client', {}).get('source_version')):
            raise ValueError('Ordered belt catalog ownership/version mismatch')
        binding = belt.get('binding', {})
        if (binding != ammo.get('native_ammunition_binding') or not binding.get('mounts') or
                binding.get('unit_path') != 'gamedata/units/tankmodels/' + vehicle['id'] + '.blk'):
            raise ValueError('Ordered belt mounted source proof mismatch')
        identities = binding.get('projectile_identities', [])
        refs = belt.get('projectiles', [])
        if not refs or len(identities) != len(refs):
            raise ValueError('Ordered belt projectile order missing')
        counts = [r.get('source_bullet_count') for r in refs]
        if (any(not isinstance(c, int) or isinstance(c, bool) or c < 1 for c in counts) or len(set(counts)) != 1 or
                belt.get('firing_order_status') != 'assumed_repeating_source_block_order_equal_counts'):
            raise ValueError('Ordered belt firing sequence not supported')
        for index, (identity, ref) in enumerate(zip(identities, refs)):
            source = {k:v for k,v in identity.items() if k != 'id'}
            if (identity.get('id') != 'source-projectile:' + digest(source) or
                    identity.get('bullet_index') != index or
                    identity.get('source_weapon_path') != binding.get('weapon_path') or
                    identity.get('source_weapon_sha256') != binding.get('weapon_sha256') or
                    identity.get('selector') != binding.get('selector') or
                    identity.get('projectile_sha256') != ref.get('projectile_sha256')):
                raise ValueError('Ordered belt projectile identity/order mismatch')
        groups = projectile_groups(ammo, tuple(profiles.values()))
        unique = {p['name']:p for group in groups for p in group}
        result.extend((ammo, p) for p in unique.values())
    return result
