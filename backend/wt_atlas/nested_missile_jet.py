"""Versioned evidence adapter for an exactly mounted nested missile jet.

The existing chemical adapter remains unchanged so previously derived range
profiles retain their source-evidence hashes and can still be reproduced.
"""
import copy
import math
from pathlib import Path

from .common import content_hash, file_hash
from .forecast.data import number, safe_file
from .native_binding.ordered_blk import as_json, parse_ordered_blk
from .chemical_projectiles import jet_source

ADAPTER = 'nested-missile-jet-v1'


def ce_profile(ammo, binding, tables, wiki, response, version, commit):
    from .ammunition_recovery import source_rows
    bullets=binding['bullets']
    if len(bullets)!=1 or not binding.get('nested_missile_rules'):
        raise ValueError('nested_missile_source_binding_missing')
    bullet=bullets[0];jet,evidence=jet_source(bullet)
    if (jet is bullet or not number(jet.get('caliber')) or not number(ammo.get('caliber_mm')) or
        not math.isclose(ammo['caliber_mm'],jet['caliber']*1000,abs_tol=.01)):
        raise ValueError('catalog_nested_rocket_caliber_mismatch')
    table=tables.get((ammo.get('provenance',{}).get('parent_weapon_name'),ammo['name']))
    constant=[p for p in (table or {}).get('points',[]) if p['distance_m'] is None]
    comparison_points=constant or (table or {}).get('points',[])
    if table is None or comparison_points!=ammo.get('penetration'):
        raise ValueError('catalog_penetration_differs_from_exact_wiki_table')
    proof={k:copy.deepcopy(v) for k,v in binding.items() if k!='bullets'}
    proof.update(ordered_projectiles=[bullet.get('bulletName')],projectile_sha256=content_hash(bullet),
        wiki=wiki,wiki_maximum_only=table['maximum_only'],
        penetration_parameter='rocket.cumulativeDamage.armorPower',
        catalog_caliber_parameter='rocket.caliber',catalog_caliber_mm=ammo['caliber_mm'],
        wiki_normal_penetration_mismatches=[p for p in comparison_points if p['angle_deg']==0 and p['mm']!=evidence['penetration_mm']],
        wiki_comparison_scope='constant_chemical_table' if constant else 'complete_penetration_table',
        wiki_additional_distance_points=[p for p in table['points'] if constant and p['distance_m'] is not None])
    dispersion=response['dispersion']
    if not number(dispersion) or not 0<=dispersion<1:raise ValueError('invalid_native_dispersion')
    assumptions={'air_loss_mm_per_m':'100 mm/m retained from the existing V2 CE model; not a verified native air-loss parameter.',
        'activation_and_delivery':'Conditional on a functioning single jet at impact. Native flight/minimum-range/fuze behavior and hit probability are not modeled.'}
    if evidence['damage_caliber_parameter']=='caliber':
        assumptions['damage_caliber_mm']='Native projectile caliber used where damageCaliber is absent, as in the existing V2 CE model.'
    result={'mode':'CE','penetration_mm':evidence['penetration_mm'],'distance_m':None,
        'normalized_distance_curve':[],'dispersion':dispersion,'damage_caliber_mm':evidence['damage_caliber_mm'],
        'mechanism_id':10,'air_loss_mm_per_m':100.,'slope_rows':[],
        'ricochet_rows':source_rows(response['ricochet'],jet['ricochetPreset'],'ricochetProbability'),
        'provenance':{'client_version':version,'datamine_commit':commit,'penetration_source_ammo_ids':[ammo['id']],
            'bullet_name':bullet.get('bulletName'),'bullet_type':bullet['bulletType'],
            'source_weapon_path':binding['weapon_path'],'source_weapon_sha256':binding['weapon_sha256'],
            'source_match_method':'exact_mounted_weapon_and_projectile_short_client_localization',
            'source_hashes':response['hashes'],'model_assumptions':assumptions,
            'chemical_energy':evidence,'nested_missile_adapter':ADAPTER,'recovery_bindings':{ammo['id']:proof}}}
    result['name']='recovered_'+content_hash(result)[:24]
    return result


def source_evidence(profile, source_root):
    """Recheck raw mount, projectile, localization receipt, and reviewed rules."""
    from .ammunition_recovery import array
    from .chemical_projectiles import source_evidence as chemical_evidence
    from .default_belts import REVIEWED_SCRIPTS
    provenance=profile.get('provenance',{})
    if provenance.get('nested_missile_adapter')!=ADAPTER:
        raise ValueError('Unsupported nested missile adapter')
    base=chemical_evidence(profile,source_root)
    root=Path(source_root)
    for ammo_id,proof in provenance.get('recovery_bindings',{}).items():
        rules=proof.get('nested_missile_rules',{});label=proof.get('nested_missile_label',{})
        if (rules.get('source_version')!=provenance.get('client_version') or
            any(rules.get('scripts',{}).get(path,{}).get('lf_sha256')!=sha for path,sha in REVIEWED_SCRIPTS.items())):
            raise ValueError('Nested missile GUI rules mismatch')
        unit=as_json(parse_ordered_blk(safe_file(root,proof['unit_path'])))
        weapon=as_json(parse_ordered_blk(safe_file(root,proof['weapon_path'])))
        mounts=[m for m in array(unit.get('commonWeapons',{}).get('Weapon'))
                if m.get('blk','').replace('\\','/').lower()==proof['weapon_path']]
        bullets=array(weapon.get('bullet'))
        if (len(mounts)!=1 or mounts[0].get('dummy') or mounts[0].get('trigger')=='countermeasures' or
            proof.get('primary_modification_id') or proof.get('selector')!='@default' or len(bullets)!=1 or
            weapon.get('isBulletBelt') is not False or weapon.get('useSingleIconForBullet') is not True or
            weapon.get('notUseDefaultBulletInGui') is not True or bullets[0].get('bulletType')!='atgm_tank' or
            not isinstance(bullets[0].get('rocket'),dict)):
            raise ValueError('Nested missile mounted weapon shape mismatch')
        bullet=bullets[0];jet=bullet['rocket'];basename=mounts[0]['blk'].replace('\\','/').rsplit('/',1)[-1].removesuffix('.blk')
        expected_keys=['weapons/'+basename,'weapons/'+str(bullet.get('bulletName'))+'/short']
        localization=proof.get('localization',[])
        if (len(localization)!=2 or [x.get('key') for x in localization]!=expected_keys or
            [x.get('value') for x in localization]!=[label.get('parent_weapon_name'),label.get('ammo_name')] or
            ammo_id.split('::',1)[-1]!=label.get('ammo_name') or
            proof.get('catalog_caliber_parameter')!='rocket.caliber' or not number(jet.get('caliber')) or
            not number(proof.get('catalog_caliber_mm')) or
            not math.isclose(proof['catalog_caliber_mm'],jet['caliber']*1000,abs_tol=.01)):
            raise ValueError('Nested missile localization or caliber evidence mismatch')
    return {**base,'nested_missile_adapter':ADAPTER,'nested_missile_adapter_sha256':file_hash(__file__)}
