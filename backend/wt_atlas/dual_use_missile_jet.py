"""Source-bound conditional jet for explicitly dual-use ATGM/SAM projectiles.

This adapter models only a single functioning shaped-charge jet at impact.
It does not model flight, activation, proximity fuzes, fragmentation or damage.
"""
import copy
import math
from pathlib import Path

from .common import content_hash, file_hash
from .forecast.data import number, safe_file
from .native_binding.ordered_blk import as_json, parse_ordered_blk

ADAPTER='dual-use-missile-jet-v1'


def jet_source(bullet):
    if not isinstance(bullet,dict) or bullet.get('bulletType')!='atgm_tank':
        raise ValueError('dual_use_outer_atgm_required')
    jet=bullet.get('rocket')
    if not isinstance(jet,dict) or jet.get('bulletType')!='sam_tank':
        raise ValueError('dual_use_nested_sam_required')
    charge=jet.get('cumulativeDamage')
    if not isinstance(charge,dict):raise ValueError('dual_use_scalar_charge_missing')
    if (charge.get('damageType','cumulative')!='cumulative' or 'cumulativeDamage' in bullet or
        any(node.get('cumulativeByNormal') or node.get('cumulativeDeviationAngle',0)!=0 or
            any('tandem' in k.lower() or 'precharge' in k.lower() for k in node)
            for node in (bullet,jet,charge))):
        raise ValueError('dual_use_multiple_or_directed_charge_unsupported')
    power=charge.get('armorPower');caliber=jet.get('damageCaliber',jet.get('caliber'))
    if not number(power) or power<=0 or not number(caliber) or caliber<=0:
        raise ValueError('dual_use_penetration_or_caliber_missing')
    if not isinstance(jet.get('ricochetPreset'),str):raise ValueError('dual_use_ricochet_preset_missing')
    return jet,{'kind':'single_jet_conditional_at_impact','projectile_sha256':content_hash(bullet),
        'jet_sha256':content_hash(jet),'jet_path':'rocket','jet_type':jet['bulletType'],
        'penetration_mm':power,'damage_caliber_mm':caliber*1000,
        'damage_caliber_parameter':'damageCaliber' if 'damageCaliber' in jet else 'caliber',
        'ricochet_preset':jet['ricochetPreset'],'source_min_distance_m':jet.get('minDistance'),
        'source_max_distance_m':jet.get('maxDistance'),'flight_and_activation_modeled':False}


def ce_profile(ammo,binding,tables,wiki,response,version,commit):
    from .ammunition_recovery import source_rows
    bullets=binding['bullets']
    if len(bullets)!=1 or binding.get('primary_modification_id'):
        raise ValueError('dual_use_exact_single_mounted_selection_required')
    bullet=bullets[0];jet,evidence=jet_source(bullet)
    labels=binding.get('localization',[])
    if not any(x.get('key')==bullet.get('bulletName') and x.get('value')==ammo.get('name') for x in labels):
        raise ValueError('dual_use_exact_projectile_localization_required')
    if not number(ammo.get('caliber_mm')) or not number(jet.get('caliber')) or not math.isclose(ammo['caliber_mm'],jet['caliber']*1000,abs_tol=.01):
        raise ValueError('dual_use_catalog_caliber_mismatch')
    table=tables.get((ammo.get('provenance',{}).get('parent_weapon_name'),ammo['name']))
    constant=[p for p in (table or {}).get('points',[]) if p['distance_m'] is None]
    comparison=constant or (table or {}).get('points',[])
    if table is None or comparison!=ammo.get('penetration'):
        raise ValueError('catalog_penetration_differs_from_exact_wiki_table')
    normal=[p['mm'] for p in comparison if p['angle_deg']==0]
    if normal!=[evidence['penetration_mm']]:raise ValueError('dual_use_wiki_source_penetration_mismatch')
    proof={k:copy.deepcopy(v) for k,v in binding.items() if k!='bullets'}
    proof.update(ordered_projectiles=[bullet.get('bulletName')],projectile_sha256=content_hash(bullet),wiki=wiki,
        wiki_maximum_only=table['maximum_only'],penetration_parameter='rocket.cumulativeDamage.armorPower',
        catalog_caliber_parameter='rocket.caliber',catalog_caliber_mm=ammo['caliber_mm'],
        wiki_normal_penetration_mismatches=[],wiki_comparison_scope='constant_chemical_table' if constant else 'complete_penetration_table',
        wiki_additional_distance_points=[p for p in table['points'] if constant and p['distance_m'] is not None])
    dispersion=response['dispersion']
    if not number(dispersion) or not 0<=dispersion<1:raise ValueError('invalid_native_dispersion')
    assumptions={'air_loss_mm_per_m':'100 mm/m retained from the existing V2 CE model; not a verified native air-loss parameter.',
        'activation_and_delivery':'Conditional on a functioning single jet at impact. Native flight/minimum-range/fuze behavior and hit probability are not modeled.'}
    if evidence['damage_caliber_parameter']=='caliber':
        assumptions['damage_caliber_mm']='Native projectile caliber used where damageCaliber is absent, as in the existing V2 CE model.'
    result={'mode':'CE','penetration_mm':evidence['penetration_mm'],'distance_m':None,'normalized_distance_curve':[],
        'dispersion':dispersion,'damage_caliber_mm':evidence['damage_caliber_mm'],'mechanism_id':10,
        'air_loss_mm_per_m':100.,'slope_rows':[],
        'ricochet_rows':source_rows(response['ricochet'],jet['ricochetPreset'],'ricochetProbability'),
        'provenance':{'client_version':version,'datamine_commit':commit,'penetration_source_ammo_ids':[ammo['id']],
            'bullet_name':bullet.get('bulletName'),'bullet_type':bullet['bulletType'],'source_weapon_path':binding['weapon_path'],
            'source_weapon_sha256':binding['weapon_sha256'],'source_match_method':'exact_mounted_selector_and_client_localization',
            'source_hashes':response['hashes'],'model_assumptions':assumptions,'chemical_energy':evidence,
            'dual_use_missile_adapter':ADAPTER,'recovery_bindings':{ammo['id']:proof}}}
    result['name']='recovered_'+content_hash(result)[:24]
    return result


def source_evidence(profile,source_root):
    from .ammunition_recovery import array,selected_projectiles,source_rows
    from .ammunition_selectors import REVIEWED_SCRIPTS
    if source_root is None:raise ValueError('Dual-use missile requires captured source')
    root=Path(source_root);prov=profile.get('provenance',{});proofs=prov.get('recovery_bindings',{})
    if prov.get('dual_use_missile_adapter')!=ADAPTER or not proofs or sorted(proofs)!=sorted(prov.get('penetration_source_ammo_ids',[])):
        raise ValueError('Dual-use missile ownership evidence missing')
    def read(path,sha):
        p=safe_file(root,path)
        if file_hash(p)!=sha:raise ValueError('Dual-use missile source hash mismatch')
        return as_json(parse_ordered_blk(p))
    response={path:read(path,sha) for path,sha in prov.get('source_hashes',{}).items()}
    ricochet=response.get('gamedata/damage_model/ricochet.blk',{});system=response.get('gamedata/damage_model/damage_system.blk',{})
    for ammo_id,proof in proofs.items():
        if proof.get('unit_path')!='gamedata/units/tankmodels/'+ammo_id.split('::',1)[0]+'.blk':
            raise ValueError('Dual-use missile vehicle mismatch')
        unit=read(proof['unit_path'],proof['unit_sha256']);weapon=read(proof['weapon_path'],proof['weapon_sha256'])
        mounts=[m for m in array(unit.get('commonWeapons',{}).get('Weapon')) if m.get('blk','').replace('\\','/').lower()==proof['weapon_path']]
        actual=[{k:m[k] for k in ('trigger','triggerGroup','emitter') if k in m} for m in mounts]
        if not mounts or any(m.get('dummy') or m.get('trigger')=='countermeasures' for m in mounts) or actual!=proof.get('mounts') or proof.get('primary_modification_id'):
            raise ValueError('Dual-use missile mount mismatch')
        selector=proof.get('selector')
        if selector!='@default':
            name=proof.get('modification_id');rules=proof.get('selector_rules',{});effect=proof.get('modification_effect',{})
            if (name not in unit.get('modifications',{}) or effect.get('kind') not in ('bulletMod','additiveBulletMod') or
                effect.get('selector')!=selector or rules.get('source_version')!=prov.get('client_version') or
                any(rules.get('scripts',{}).get(path,{}).get('lf_sha256')!=sha for path,sha in REVIEWED_SCRIPTS.items())):
                raise ValueError('Dual-use missile selector rules mismatch')
        bullets=selected_projectiles(weapon,selector,proof.get('projectile_block'))
        if len(bullets)!=1 or content_hash(bullets[0])!=proof.get('projectile_sha256'):
            raise ValueError('Dual-use raw projectile source mismatch')
        bullet=bullets[0];jet,evidence=jet_source(bullet)
        if evidence!=prov.get('chemical_energy'):raise ValueError('Dual-use missile jet evidence mismatch')
        if (not any(x.get('key')==bullet.get('bulletName') and x.get('value')==ammo_id.split('::',1)[-1] for x in proof.get('localization',[])) or
            proof.get('catalog_caliber_parameter')!='rocket.caliber' or not number(proof.get('catalog_caliber_mm')) or
            not number(jet.get('caliber')) or not math.isclose(proof['catalog_caliber_mm'],jet['caliber']*1000,abs_tol=.01)):
            raise ValueError('Dual-use missile localization or caliber mismatch')
        physical={'penetration_mm':evidence['penetration_mm'],'damage_caliber_mm':evidence['damage_caliber_mm'],
            'mechanism_id':10,'air_loss_mm_per_m':100.,'slope_rows':[],'normalized_distance_curve':[],
            'dispersion':system.get('pierceDispersion'),'ricochet_rows':source_rows(ricochet,jet['ricochetPreset'],'ricochetProbability')}
        if profile.get('mode')!='CE' or any(profile.get(k)!=v for k,v in physical.items()):
            raise ValueError('Dual-use missile physical profile mismatch')
    return {'chemical_energy':prov['chemical_energy'],'recovery_bindings_sha256':content_hash(proofs),
        'dual_use_missile_adapter':ADAPTER,'dual_use_missile_adapter_sha256':file_hash(__file__),
        'source_hashes':prov['source_hashes'],'parameter':'rocket.cumulativeDamage.armorPower',
        'penetration_mm':profile['penetration_mm']}
