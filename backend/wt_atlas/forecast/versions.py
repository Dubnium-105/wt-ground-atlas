"""Auditable snapshot states and layer-specific invalidation decisions."""
from __future__ import annotations
from datetime import datetime
from .data import digest, patch_catalog, number, mission_policy
from .delivery import delivery_issue
from .rankings import table_candidates

STATUSES={'released','announced','proposed','hypothetical','unknown'}


def snapshot(catalog):
    s=catalog.get('snapshot',{})
    record={
        'snapshot_id':s.get('snapshot_id',s.get('id')),
        'game_version':s.get('game_version',s.get('client_source',{}).get('version')),
        'datamine_commit':s.get('datamine_commit',s.get('datamine_enrichment',{}).get('ref')),
        'wiki_collected_at':s.get('wiki_collected_at',s.get('collected_at')),
        'effective_date':s.get('effective_date'),
        'source_status':s.get('source_status','unknown'),
        'sources':s.get('sources',s.get('captures',[]))}
    if record['source_status'] not in STATUSES:raise ValueError('Unknown snapshot source status')
    for key in ('wiki_collected_at','effective_date'):
        if record[key]:datetime.fromisoformat(record[key].replace('Z','+00:00'))
    record['sha256']=digest(record)
    return record


def invalidation(before,after):
    old={v['id']:v for v in before['vehicles']};new={v['id']:v for v in after['vehicles']}
    rows=[]
    for identifier in sorted(old.keys()|new.keys()):
        a,b=old.get(identifier),new.get(identifier)
        if a is None or b is None:
            rows.append({'vehicle_id':identifier,'change':'added' if a is None else 'removed','layers':['physics','shot','pair','mission','lineup','rank']});continue
        fields=[k for k in sorted(a.keys()|b.keys()) if digest(a.get(k))!=digest(b.get(k))]
        if not fields:continue
        physics=any(k in fields for k in ('ammo','protection','client_collision','analysis_target_sha256','source_attributes'))
        timing=any(k in fields for k in ('firepower','mobility','sensors'))
        layers=['physics','shot','pair','mission','lineup','rank'] if physics else ['shot','pair','mission','lineup','rank'] if timing else ['mission','lineup','rank']
        rows.append({'vehicle_id':identifier,'fields':fields,'layers':layers,'reuse_physical_maps':not physics})
    return rows


def apply_version(catalog,patch):
    if patch is None:return catalog,None,[]
    status=patch.get('status','unknown')
    if status not in STATUSES:raise ValueError('Unknown scenario status')
    changed,audit=patch_catalog(catalog,patch)
    if status=='released':
        # An implemented adjustment is the baseline, not a future prediction.
        changed.setdefault('snapshot',{}).update(source_status='released',effective_date=patch.get('effective_date'),
                                               id=patch.get('snapshot_id',digest([catalog.get('snapshot'),patch])))
        return changed,None,audit
    return catalog,changed,audit


def selection_identity(variant):
    return tuple(variant.get(k) for k in ('vehicle_id','rating_configuration_id','loadout_id','ammo_id'))


def publication_rows(world):
    """Include filtered/category views, not only the representative top board."""
    rows = [r for board in world['boards'].values() for r in board]
    rows += [r for view in world.get('views', {}).values() for board in view.values() for r in board]
    rows += [r for table in world.get('tables', []) if not table['budget_exhausted']
             for r in table_candidates(table) if r.get('rankable')]
    return rows


def source_audit(catalog, config, version, variants, rows):
    """Audit eligible source rules and prove published members were enumerated.

    Illegal presets remain diagnostics, not a global publication veto. Missing
    source evidence on legal presets and unknown opponent configurations remain
    visible; no candidate is removed to improve a coverage denominator.
    """
    blockers = set(); excluded = []; unknown_ratings = []; legal = 0; quarantined = 0; checked = 0
    def sourced(rule):
        return (isinstance(rule, dict) and rule.get('status') == 'official_verified' and
                rule.get('source_version') == version and bool(rule.get('source')))
    for vehicle in catalog['vehicles']:
        if vehicle.get('system_role') == 'component':
            excluded.append(vehicle['id']); continue
        checked += 1
        if config['spawn'].get('status') == 'official_verified' and not sourced(vehicle.get('spawn_rule', {})):
            blockers.add('vehicle_spawn_provenance_incomplete')
        br = vehicle.get('br_provenance', {})
        if br.get('source_version') != version or not br.get('source'):
            blockers.add('rating_version_provenance_incomplete')
        block = vehicle.get('rating_configurations', {}).get('ground_rb')
        if block is not None and block.get('source_version') != version:
            blockers.add('rating_configuration_version_unverified')
        if block is not None and (block.get('status') != 'official_verified' or not block.get('source', {}).get('url')):
            blockers.add('rating_configuration_source_unverified')
        ratings = [option.get('br', {}).get('current') for option in block.get('options', [])] if block is not None else [vehicle.get('br', {}).get('current')]
        if not ratings or any(not number(value) for value in ratings):
            unknown_ratings.append(vehicle['id'])
            blockers.add('ground_rb_rating_or_eligibility_unknown')
        for loadout in vehicle.get('loadouts', []):
            if loadout.get('legal') is not True:
                quarantined += 1; continue
            legal += 1
            if (loadout.get('source_version') != version or loadout.get('source_status') != 'official_verified'
                    or not loadout.get('source')):
                blockers.add('loadout_source_unverified')
            # spawn_cost() gives a carried payload's rule precedence over the
            # vehicle rule. A verified vehicle must not mask an assumed override.
            if config['spawn'].get('status') == 'official_verified' and 'spawn_rule' in loadout:
                if not sourced(loadout['spawn_rule'] or {}):
                    blockers.add('loadout_spawn_provenance_incomplete')
    # A complete array inventory alone cannot establish delivery at distances
    # outside the native missile's declarations. Do not remove such variants
    # to make the global ranking denominator look complete.
    configured_distances = {s['physical_distance_m'] for s in config.get('scenarios', [])}
    delivery_gaps = {}
    for variant in variants:
        profiles = variant.get('profiles', (variant['profile'],) if variant.get('profile') else ())
        for profile in profiles:
            distance = profile.get('distance_m')
            if distance not in configured_distances: continue
            issue = delivery_issue(profile, distance)
            if issue:
                blockers.add(issue)
                key = (variant['vehicle_id'], variant.get('ammo_id') or '', profile.get('name') or '', distance, issue)
                delivery_gaps[key] = {'vehicle_id': variant['vehicle_id'], 'ammo_id': variant.get('ammo_id'),
                    'profile_id': profile.get('name'), 'distance_m': distance, 'reason': issue}
    by_key = {v['key']: v for v in variants}
    if len(by_key) != len(variants): blockers.add('ambiguous_variant_identity')
    selected = set()
    for row in rows:
        members = row.get('members')
        if not isinstance(members, list) or not members:
            blockers.add('ranked_configuration_not_verified'); continue
        for member in members:
            variant = by_key.get(member.get('key'))
            if (variant is None or not variant.get('selection_ready') or
                selection_identity(member) != selection_identity(variant) or
                member.get('version') != version or member.get('mode') != 'ground_rb' or
                member.get('br') != variant['br']):
                blockers.add('ranked_configuration_not_verified'); continue
            if 'loadout' in member and digest(member['loadout']) != digest(variant.get('loadout')):
                blockers.add('ranked_configuration_not_verified'); continue
            selected.add(variant['key'])
    return {'blockers': sorted(blockers), 'checked_vehicles': checked,
            'excluded_component_ids': sorted(excluded), 'unknown_rating_or_eligibility_ids': sorted(unknown_ratings), 'checked_legal_loadouts': legal,
            'quarantined_loadouts': quarantined, 'verified_ranked_configurations': len(selected),
            'unknown_opponent_configurations': sum(not v.get('selection_ready') for v in variants),
            'delivery_unverified_profiles': [delivery_gaps[key] for key in sorted(delivery_gaps)]}


def release_gate(catalog,atlas,config,world,*,inventory=None,missions=None,sensitivity=None,sensitivity_inputs=None,variants=None):
    info=snapshot(catalog); blockers=[]
    policy=mission_policy(config)
    require_missions=policy['mode']=='require_evidence'
    synthetic=catalog.get('test_fixture') is True
    if synthetic:blockers.append('synthetic_fixture_not_production')
    if catalog.get('sample_subset'):blockers.append('sample_subset')
    if not info['snapshot_id'] or not info['game_version']:blockers.append('snapshot_identity_missing')
    if info['source_status']!='released':blockers.append('baseline_release_status_unverified')
    if not info['datamine_commit'] or not info['wiki_collected_at'] or not info['effective_date']:blockers.append('source_version_timeline_incomplete')
    bindings={m.get('target_binding') for m in atlas.meta.values()}
    versions={(m.get('source_version'),m.get('datamine_commit')) for m in atlas.meta.values()}
    if versions!={(info['game_version'],info['datamine_commit'])}:blockers.append('snapshot_physics_version_mismatch')
    if not bindings or bindings!={'independent_geometry_verified'}:blockers.append('independent_target_geometry_not_verified')
    if inventory is None or inventory['verified_arrays']!=inventory['selected_maps'] or not inventory['selected_maps']:blockers.append('physical_arrays_not_fully_verified')
    if world['truncated_searches']:blockers.append('search_budget_truncated')
    if not any(world['boards'].values()):blockers.append('no_candidates_meet_bidirectional_coverage')
    if config['publication']['scope']!='full_ground_rb':blockers.append('scope_is_limited_ground_research')
    tasks=config['publication'].get('required_tasks',['ground_control','anti_tank','recon','anti_helicopter','anti_air','cap','cas','sead'])
    evidence_tasks={r.get('task') for r in (missions or {}).values() if r.get('production_validated')}
    # Ground anti-tank evidence comes from exact bidirectional physical maps.
    # Per-lineup coverage below still rejects missing air anti-tank delivery data.
    if require_missions and not set(tasks)<=evidence_tasks|{'ground','anti_tank'}:blockers.append('full_mode_mission_evidence_missing')
    ranked_rows = publication_rows(world)
    if config['publication']['scope']=='full_ground_rb':
        from .tasks import modeled_tasks
        modeled=modeled_tasks(config['spawn'])
        if not set(tasks)<=modeled:blockers.append('required_tasks_not_modeled')
        threshold=config['search']['minimum_pair_coverage']
        if require_missions and any(not number(row.get('production_mission_coverage',{}).get(task)) or
               not threshold<=row['production_mission_coverage'][task]<=1
               for row in ranked_rows for task in tasks):
            blockers.append('ranked_lineup_mission_evidence_incomplete')
    if config['spawn'].get('status')!='official_verified':blockers.append('spawn_rules_assumed')
    if variants is None:
        from .engine import enumerate_variants
        variants, _ = enumerate_variants(catalog, atlas, config)
    audit = source_audit(catalog, config, info['game_version'], variants, ranked_rows)
    blockers.extend(audit['blockers'])
    from .sensitivity import valid
    if not valid(sensitivity,catalog,config,sensitivity_inputs,world=world):blockers.append('sensitivity_not_verified')
    if config.get('endpoint')=='damage' and config.get('damage_status')!='validated':blockers.append('damage_evidence_unvalidated')
    if config.get('calibration_status')=='validated' and not config.get('calibration',{}).get('independent_report_sha256'):
        blockers.append('unsupported_calibration_claim')
    return {'algorithm_ready':True,'data_ready':not any(b for b in blockers if b not in ('search_budget_truncated','sensitivity_not_verified')),
            'publishable':not blockers,'why_not_publishable':blockers,'snapshot':info,
            'source_audit':audit,
            'calibration_status':config['calibration_status'],'empirical_win_rate_validated':False,
            'mission_policy':policy,'mission_evidence_required':require_missions,
            'prediction_basis':'evidence_required' if require_missions else 'explicit_mission_assumptions',
            'research_candidates':sum(len(x) for x in world['boards'].values())}
