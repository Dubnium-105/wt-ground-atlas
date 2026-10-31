"""Evidence for independent tasks, complete carried payloads and sortie costs."""
from .data import digest, number
import re
from .mount_rules import check_mounts

TASKS={'ground','anti_tank','ground_control','recon','anti_helicopter','anti_air','cap','cas','sead'}


def validate_mission(row,version,variants,scenarios):
    required=('variant_key','target_vehicle_id','scenario_id','task','lower','mean','upper','source_version','source','status')
    if any(k not in row for k in required):raise ValueError('Incomplete mission evidence')
    if row['status'] not in ('validated','assumed','missing','unvalidated'):raise ValueError('Unknown mission status')
    if row['source_version']!=version or not row['source']:raise ValueError('Mission source/version mismatch')
    if row['variant_key'] not in variants or row['scenario_id'] not in scenarios or row['task'] not in TASKS:raise ValueError('Mission identity/task mismatch')
    if not all(number(row[k]) and 0<=row[k]<=1 for k in ('lower','mean','upper')) or not row['lower']<=row['mean']<=row['upper']:raise ValueError('Invalid mission interval')
    evidence=row.get('evidence')
    if evidence is not None:
        for k in ('loadout_id','flight_conditions','radar_conditions','target_countermeasures','source_sha256'):
            if k not in evidence:raise ValueError('Incomplete mission delivery evidence: '+k)
        if evidence['loadout_id']!=variants[row['variant_key']].get('loadout_id'):raise ValueError('Mission carried payload mismatch')
        if not isinstance(evidence['source_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',evidence['source_sha256']):raise ValueError('Mission source hash required')
        if any(not isinstance(evidence[k],dict) for k in ('flight_conditions','radar_conditions','target_countermeasures')):raise ValueError('Mission conditions must be objects')
        if evidence.get('measurement')=='maximum_range':raise ValueError('Maximum range is not observed mission effectiveness')
    return {**row,'evidence_sha256':digest(row),'production_validated':row['status']=='validated' and evidence is not None}


def loadout_errors(loadout,vehicle,option,*,unlocked=None,require_unlocked=False):
    errors=[]
    if vehicle.get('mount_rule_audit',{}).get('status')=='unsupported_source_constraints':
        errors.append('unsupported_source_mount_constraints')
    if vehicle.get('mount_rule') is not None:
        errors.extend(check_mounts(loadout.get('mounts'),vehicle['mount_rule'])['errors'])
    if loadout.get('legal') is not True:errors.append('loadout_not_verified_legal')
    weapons=loadout.get('weapons',[])
    stations=[w['station'] for w in weapons if w.get('station') is not None]
    if len(stations)!=len(set(stations)):errors.append('mutually_exclusive_station')
    ids={w.get('id') for w in weapons}
    if None in ids:errors.append('weapon_identity_missing')
    for w in weapons:
        if not isinstance(w.get('count'),int) and (not number(w.get('count')) or int(w['count'])!=w['count']):errors.append('invalid_weapon_count')
        if not number(w.get('count')) or w['count']<=0:errors.append('invalid_weapon_count')
        if any(x in ids for x in w.get('conflicts_with',[])):errors.append('mutually_exclusive_weapon')
        if any(x not in ids for x in w.get('requires',[])):errors.append('weapon_dependency_missing')
    capacity=loadout.get('max_payload_kg',vehicle.get('max_payload_kg'))
    if number(capacity) and all(number(w.get('mass_kg')) and number(w.get('count')) for w in weapons):
        if sum(w['mass_kg']*w['count'] for w in weapons)>capacity:errors.append('payload_capacity_exceeded')
    for field,chosen in [('allowed_loadout_ids',[loadout.get('id')]),('allowed_weapon_ids',ids)]:
        if field in option and any(x not in option[field] for x in chosen):errors.append('forbidden_by_rating_configuration')
    required=set(option.get('required_modification_ids',[]))|set(loadout.get('required_modification_ids',[]))
    if require_unlocked and (unlocked is None or not required<=set(unlocked)):errors.append('modifications_unavailable')
    return sorted(set(errors))


def spawn_group(variant):
    """Declared spawn category can differ by carried payload; never infer from role."""
    source=(variant.get('loadout') or {}).get('spawn_rule',variant['vehicle'].get('spawn_rule')) or {}
    group=source.get('spawn_group')
    if group is not None and (not isinstance(group,str) or not group.strip()):
        raise ValueError('Invalid spawn group')
    return group if group is not None else variant['domain']


def spawn_cost(variant,spawn,room_top,respawns=0):
    if not isinstance(respawns,int) or isinstance(respawns,bool) or respawns<0:
        raise ValueError('Previous spawn count must be a nonnegative integer')
    source=(variant.get('loadout') or {}).get('spawn_rule',variant['vehicle'].get('spawn_rule'))
    model='multiplier'
    if source:
        if source.get('status') not in ('official_verified','assumed') or not source.get('source'):raise ValueError('Unsourced spawn rule')
        if source['status']=='official_verified' and source.get('source_version')!=variant.get('version'):raise ValueError('Spawn source version mismatch')
        cost=source.get('base_points')
        room_steps=room_top-variant['br_step']
        cost=source.get('room_costs',{}).get(str(room_steps),cost)
        increase=source.get('respawn_multiplier',1)
        model=source.get('respawn_model','multiplier')
        if model=='power_of_count':
            # The pinned client's get_spawn_score_type_mul uses (1+n)^spawn_pow.
            # Its n is counted per declared spawn category, not per vehicle/domain.
            power=source.get('respawn_power')
            if not number(power) or power<0 or not source.get('spawn_group'):
                raise ValueError('Count-power spawn rule requires a power and spawn group')
            if 'respawn_multiplier' in source:
                raise ValueError('Ambiguous respawn escalation models')
            spawn_group(variant)
        elif model!='multiplier':raise ValueError('Unknown respawn escalation model')
    else:
        cost=(variant.get('loadout') or {}).get('spawn_cost',variant['vehicle'].get('spawn_cost'))
        if cost is None:cost=spawn['ground_cost' if variant['domain']=='ground' else 'air_cost']
        increase=spawn.get('respawn_multiplier',{}).get(variant['domain'],1)
    if cost is None:return None
    if not number(cost) or cost<0 or int(cost)!=cost or not number(increase) or increase<1:raise ValueError('Invalid spawn point rule')
    factor=(1+respawns)**power if model=='power_of_count' else increase**respawns
    return int(round(cost*factor))
