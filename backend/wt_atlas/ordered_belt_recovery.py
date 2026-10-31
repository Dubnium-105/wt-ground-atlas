"""Reuse full raw-projectile identities from verified homogeneous recovery."""
import copy
from .common import content_hash
from .projectile_identity import identities


def component_index(profiles, adapter):
    from .ammunition_recovery import array
    result = {}
    for profile in profiles:
        provenance = profile.get('provenance', {})
        if provenance.get('range_rebuild') or profile.get('reference_only'): continue
        proofs = provenance.get('recovery_bindings', {})
        if not proofs: continue  # No name/caliber inference for legacy profiles.
        hashes = set()
        for proof in proofs.values():
            weapon = adapter.read(proof['weapon_path'])
            selected = weapon if proof['selector'] == '@default' else weapon.get(proof['selector'], {})
            bullets = array(selected.get('bullet'))
            if (adapter.sources[proof['weapon_path']] != proof['weapon_sha256'] or not bullets or
                    {content_hash(b) for b in bullets} != {proof.get('projectile_sha256')}):
                raise ValueError('Recovered component raw projectile source changed')
            hashes.add(proof['projectile_sha256'])
        if len(hashes) != 1: raise ValueError('Recovered component has conflicting raw identities')
        result.setdefault(hashes.pop(), []).append(profile)
    return result


def recover_belt(vehicle, ammo, binding, components, version, commit, wiki):
    bullets = binding['bullets']; refs = []
    counts = [b.get('bulletCount') for b in bullets]
    if any(not isinstance(c, int) or isinstance(c, bool) or c < 1 for c in counts):
        raise ValueError('ordered_belt_component_counts_missing')
    if len(set(counts)) != 1:
        raise ValueError('ordered_belt_unequal_component_counts_require_native_sequence')
    for bullet in bullets:
        key = content_hash(bullet); candidates = components.get(key, [])
        if not candidates: raise ValueError('ordered_belt_component_profile_missing')
        if len(candidates) != 1: raise ValueError('ordered_belt_component_profile_ambiguous')
        profile = candidates[0]; provenance = profile['provenance']
        if (provenance.get('client_version'), provenance.get('datamine_commit')) != (version, commit):
            raise ValueError('ordered_belt_component_version_mismatch')
        refs.append({'projectile_sha256':key, 'profile_name':profile['name'], 'profile_sha256':content_hash(profile),
                     'source_bullet_count':bullet['bulletCount']})
    evidence = {k:copy.deepcopy(v) for k,v in binding.items() if k != 'bullets'}
    evidence['projectile_identities'] = identities(binding['weapon_path'], binding['weapon_sha256'], binding['selector'], bullets)
    evidence['wiki'] = copy.deepcopy(wiki)
    if not evidence['projectile_identities']: raise ValueError('ordered_belt_source_identity_missing')
    ammo['native_ammunition_binding'] = evidence
    ammo['ordered_belt'] = {'schema_version':'1.0.0', 'kind':'exact_source_ordered_belt',
        'vehicle_id':vehicle['id'], 'ammo_id':ammo['id'], 'source_version':version,
        'datamine_commit':commit, 'binding':copy.deepcopy(evidence), 'projectiles':refs,
        'maximum_table_used':False, 'burst_start_status':'assumed_first_source_projectile',
        'firing_order_status':'assumed_repeating_source_block_order_equal_counts'}
    weapon = [w for w in vehicle.get('weapon_profiles', []) if w['id'] == ammo.get('weapon_id')]
    if len(weapon) == 1: ammo['weapon_profile'] = copy.deepcopy(weapon[0])
    return ammo['ordered_belt']


def verify_existing_belts(catalog, components, adapter, wiki_root, version, commit):
    """Recreate every existing receipt; an incremental run cannot retain stale belts."""
    from .ammunition_recovery import verify_wiki
    for vehicle in catalog['vehicles']:
        selected = [a for a in vehicle.get('ammo', []) if a.get('ordered_belt')]
        if not selected: continue
        candidates = adapter.candidates(vehicle)
        _, wiki = verify_wiki(vehicle, wiki_root)
        for ammo in selected:
            fresh = recover_belt(vehicle, copy.deepcopy(ammo), adapter.bind(candidates, ammo), components, version, commit, wiki)
            if fresh != ammo['ordered_belt'] or fresh['binding'] != ammo.get('native_ammunition_binding'):
                raise ValueError('Existing ordered belt source changed; reimport from original catalog')
