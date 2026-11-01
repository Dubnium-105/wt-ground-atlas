"""Source-bound single shaped-charge jets for the existing V2 CE model.

This adapter computes penetration conditional on a functioning jet at impact.
It does not model missile flight, fuzes, tandem charges, EFPs or damage.
"""
import copy
import math
from pathlib import Path

from .common import content_hash, file_hash
from .forecast.data import number, safe_file
from .native_binding.ordered_blk import as_json, parse_ordered_blk

JET_TYPES = {'heat_tank', 'heat_fs_tank', 'heat_grenade_tank', 'heat_mp_vt_tank',
             'he_or_tank', 'atgm_tank'}


def jet_source(bullet):
    if not isinstance(bullet, dict): raise ValueError('chemical_projectile_invalid')
    jet = bullet.get('rocket', bullet)
    if not isinstance(jet, dict): raise ValueError('chemical_rocket_block_invalid')
    if bullet.get('bulletType') not in JET_TYPES or jet.get('bulletType') not in JET_TYPES:
        raise ValueError('chemical_single_jet_type_unsupported')
    charge = jet.get('cumulativeDamage')
    if not isinstance(charge, dict): raise ValueError('chemical_scalar_charge_missing')
    # HESH, EFP/top-attack and tandem semantics cannot be collapsed into this jet.
    if (charge.get('damageType', 'cumulative') != 'cumulative' or
        any(node.get('cumulativeByNormal') or node.get('cumulativeDeviationAngle', 0) != 0 or
            any('tandem' in k.lower() or 'precharge' in k.lower() for k in node)
            for node in (bullet, jet, charge)) or
        (jet is not bullet and 'cumulativeDamage' in bullet)):
        raise ValueError('chemical_multiple_or_directed_charge_unsupported')
    penetration = charge.get('armorPower')
    caliber = jet.get('damageCaliber', jet.get('caliber'))
    if not number(penetration) or penetration <= 0 or not number(caliber) or caliber <= 0:
        raise ValueError('chemical_penetration_or_caliber_missing')
    if not isinstance(jet.get('ricochetPreset'), str): raise ValueError('chemical_ricochet_preset_missing')
    return jet, {'kind': 'single_jet_conditional_at_impact',
        'projectile_sha256': content_hash(bullet), 'jet_sha256': content_hash(jet),
        'jet_path': 'rocket' if jet is not bullet else '@projectile',
        'jet_type': jet['bulletType'], 'penetration_mm': penetration,
        'damage_caliber_mm': caliber * 1000,
        'damage_caliber_parameter': 'damageCaliber' if 'damageCaliber' in jet else 'caliber',
        'ricochet_preset': jet['ricochetPreset'],
        'source_min_distance_m': jet.get('minDistance'), 'source_max_distance_m': jet.get('maxDistance'),
        'flight_and_activation_modeled': False}


def ce_profile(ammo, binding, tables, wiki, response, version, commit):
    from .ammunition_recovery import source_rows
    bullets = binding['bullets']
    if not bullets or len({content_hash(b) for b in bullets}) != 1:
        raise ValueError('chemical_mixed_selection_requires_individual_profiles')
    bullet = bullets[0]; jet, evidence = jet_source(bullet)
    if (not number(ammo.get('caliber_mm')) or not number(bullet.get('caliber')) or
        not math.isclose(ammo['caliber_mm'], bullet['caliber'] * 1000, abs_tol=.01)):
        raise ValueError('catalog_native_caliber_mismatch')
    table = tables.get((ammo.get('provenance', {}).get('parent_weapon_name'), ammo['name']))
    # These popovers can contain two distinct tables: a constant chemical jet
    # and a distance-dependent kinetic impact. The catalog keeps the former.
    constant = [p for p in (table or {}).get('points', []) if p['distance_m'] is None]
    comparison_points = constant or (table or {}).get('points', [])
    if table is None or comparison_points != ammo.get('penetration'):
        raise ValueError('catalog_penetration_differs_from_exact_wiki_table')
    # Wiki is an independently retained comparison, never the jet's identity.
    comparison = [p for p in comparison_points if p['angle_deg'] == 0 and p['mm'] != evidence['penetration_mm']]
    proof = {k: copy.deepcopy(v) for k, v in binding.items() if k != 'bullets'}
    proof.update(ordered_projectiles=[b.get('bulletName') for b in bullets],
        projectile_sha256=content_hash(bullet), wiki=wiki, wiki_maximum_only=table['maximum_only'],
        penetration_parameter='rocket.cumulativeDamage.armorPower' if evidence['jet_path']=='rocket' else 'cumulativeDamage.armorPower',
        wiki_normal_penetration_mismatches=comparison,
        wiki_comparison_scope='constant_chemical_table' if constant else 'complete_penetration_table',
        wiki_additional_distance_points=[p for p in table['points'] if constant and p['distance_m'] is not None])
    assumptions = {'air_loss_mm_per_m': '100 mm/m retained from the existing V2 CE model; not a verified native air-loss parameter.',
        'activation_and_delivery': 'Conditional on a functioning single jet at impact. Native flight/minimum-range/fuze behavior and hit probability are not modeled.'}
    if evidence['damage_caliber_parameter'] == 'caliber':
        assumptions['damage_caliber_mm'] = 'Native projectile caliber used where damageCaliber is absent, as in the existing V2 CE model.'
    dispersion = response['dispersion']
    if not number(dispersion) or not 0 <= dispersion < 1: raise ValueError('invalid_native_dispersion')
    result = {'mode':'CE', 'penetration_mm':evidence['penetration_mm'], 'distance_m':None,
        'normalized_distance_curve':[], 'dispersion':dispersion,
        'damage_caliber_mm':evidence['damage_caliber_mm'], 'mechanism_id':10,
        'air_loss_mm_per_m':100., 'slope_rows':[],
        'ricochet_rows':source_rows(response['ricochet'], jet['ricochetPreset'], 'ricochetProbability'),
        'provenance':{'client_version':version,'datamine_commit':commit,
            'penetration_source_ammo_ids':[ammo['id']], 'bullet_name':bullet.get('bulletName'),
            'bullet_type':bullet['bulletType'], 'source_weapon_path':binding['weapon_path'],
            'source_weapon_sha256':binding['weapon_sha256'],
            'source_match_method':'exact_mounted_selector_and_client_localization',
            'source_hashes':response['hashes'], 'model_assumptions':assumptions,
            'chemical_energy':evidence, 'recovery_bindings':{ammo['id']:proof}}}
    result['name'] = 'recovered_' + content_hash(result)[:24]
    return result


def source_evidence(profile, source_root):
    """Re-read raw projectiles and response parameters before range derivation."""
    from .ammunition_recovery import array, source_rows
    if source_root is None: raise ValueError('CE range requires the captured source weapon file')
    root=Path(source_root); provenance=profile['provenance']; proofs=provenance.get('recovery_bindings', {})
    if not proofs or sorted(proofs)!=sorted(provenance.get('penetration_source_ammo_ids', [])):
        raise ValueError('Chemical source ownership evidence missing')
    expected = provenance.get('chemical_energy'); sources={}
    def read(path, sha):
        source=safe_file(root,path)
        if file_hash(source)!=sha: raise ValueError('Chemical source hash mismatch')
        if path not in sources: sources[path]=as_json(parse_ordered_blk(source))
        return sources[path]
    response = {}
    for path, sha in provenance.get('source_hashes', {}).items(): response[path]=read(path,sha)
    ricochet=response.get('gamedata/damage_model/ricochet.blk', {})
    system=response.get('gamedata/damage_model/damage_system.blk', {})
    for ammo_id, proof in proofs.items():
        if proof['unit_path']!='gamedata/units/tankmodels/'+ammo_id.split('::',1)[0]+'.blk':
            raise ValueError('Chemical source vehicle ownership mismatch')
        unit=read(proof['unit_path'],proof['unit_sha256'])
        mounted={w.get('blk','').replace('\\','/').lower() for w in array(unit.get('commonWeapons',{}).get('Weapon'))}
        if proof['weapon_path'] not in mounted: raise ValueError('Chemical source weapon is not mounted')
        weapon=read(proof['weapon_path'],proof['weapon_sha256'])
        selected=weapon if proof['selector']=='@default' else weapon.get(proof['selector'],{})
        bullets=array(selected.get('bullet'))
        if not bullets or {content_hash(b) for b in bullets}!={proof['projectile_sha256']}:
            raise ValueError('Chemical raw projectile source mismatch')
        jet,evidence=jet_source(bullets[0])
        if evidence!=expected: raise ValueError('Chemical jet evidence mismatch')
        physical={'penetration_mm':evidence['penetration_mm'],'damage_caliber_mm':evidence['damage_caliber_mm'],
            'mechanism_id':10,'air_loss_mm_per_m':100.,'slope_rows':[],
            'normalized_distance_curve':[],'dispersion':system.get('pierceDispersion'),
            'ricochet_rows':source_rows(ricochet,jet['ricochetPreset'],'ricochetProbability')}
        if profile.get('mode')!='CE' or any(profile.get(k)!=v for k,v in physical.items()):
            raise ValueError('Chemical physical profile differs from verified source/model')
    return {'chemical_energy':expected, 'recovery_bindings_sha256':content_hash(proofs),
            'adapter_sha256':file_hash(__file__),
            'source_hashes':provenance['source_hashes'], 'parameter':'cumulativeDamage.armorPower',
            'penetration_mm':profile['penetration_mm']}
