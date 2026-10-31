"""Auditable snapshot states and layer-specific invalidation decisions."""
from __future__ import annotations
from datetime import datetime
from .data import digest, patch_catalog, number, mission_policy
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


def release_gate(catalog,atlas,config,world,*,inventory=None,missions=None,sensitivity=None,sensitivity_inputs=None):
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
    if require_missions and not set(tasks)<=evidence_tasks:blockers.append('full_mode_mission_evidence_missing')
    ranked_rows = [r for rows in world['boards'].values() for r in rows]
    ranked_rows += [r for t in world.get('tables', []) if not t['budget_exhausted']
                    for r in table_candidates(t) if r.get('rankable')]
    if config['publication']['scope']=='full_ground_rb':
        modeled={task for task,weight in zip(config['spawn']['phases'],config['spawn']['phase_weights']) if weight>0}
        if not set(tasks)<=modeled:blockers.append('required_tasks_not_modeled')
        threshold=config['search']['minimum_pair_coverage']
        if require_missions and any(not number(row.get('production_mission_coverage',{}).get(task)) or
               not threshold<=row['production_mission_coverage'][task]<=1
               for row in ranked_rows for task in tasks):
            blockers.append('ranked_lineup_mission_evidence_incomplete')
    if config['spawn'].get('status')!='official_verified':blockers.append('spawn_rules_assumed')
    if config['spawn'].get('status')=='official_verified':
        for v in catalog['vehicles']:
            rule=v.get('spawn_rule',{})
            if rule.get('status')!='official_verified' or rule.get('source_version')!=info['game_version'] or not rule.get('source'):
                blockers.append('vehicle_spawn_provenance_incomplete');break
    for v in catalog['vehicles']:
        if any(l.get('source_version')!=info['game_version'] or l.get('source_status')!='official_verified' or not l.get('source') for l in v.get('loadouts',[])):
            blockers.append('loadout_source_unverified');break
    from .sensitivity import valid
    if not valid(sensitivity,catalog,config,sensitivity_inputs,world=world):blockers.append('sensitivity_not_verified')
    if config.get('endpoint')=='damage' and config.get('damage_status')!='validated':blockers.append('damage_evidence_unvalidated')
    if config.get('calibration_status')=='validated' and not config.get('calibration',{}).get('independent_report_sha256'):
        blockers.append('unsupported_calibration_claim')
    # Every single-BR/loadout provenance is audited, even where variants are legacy fixed BR.
    for v in catalog['vehicles']:
        br_source=v.get('br_provenance',{})
        if br_source.get('source_version')!=info['game_version'] or not br_source.get('source'):
            blockers.append('rating_version_provenance_incomplete');break
        block=v.get('rating_configurations',{}).get('ground_rb')
        if block and block.get('source_version')!=info['game_version']:
            blockers.append('rating_configuration_version_unverified');break
    return {'algorithm_ready':True,'data_ready':not any(b for b in blockers if b not in ('search_budget_truncated','sensitivity_not_verified')),
            'publishable':not blockers,'why_not_publishable':blockers,'snapshot':info,
            'calibration_status':config['calibration_status'],'empirical_win_rate_validated':False,
            'mission_policy':policy,'mission_evidence_required':require_missions,
            'prediction_basis':'evidence_required' if require_missions else 'explicit_mission_assumptions',
            'research_candidates':sum(len(x) for x in world['boards'].values())}
