import copy
import csv
import gzip
import hashlib
from html import escape
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.ammunition_recovery import (Localization, MountedAmmunition, ke_profile, consolidate,
    merge_recovered, recover, source_rows, verify_wiki, wiki_tables)
from wt_atlas.common import file_hash, write_json, read_json
from wt_atlas.default_belts import REVIEWED_SCRIPTS, verified_rules
from wt_atlas.ammunition_selectors import selector_effects, verified_rules as verified_selectors
from wt_atlas.native_binding.ordered_blk import parse_ordered_blk


def blk(data, indent=''):
    rows = []
    for key, value in data.items():
        if isinstance(value, tuple):
            rows.append(indent + key + ':p2 = ' + ', '.join(map(str, value))); continue
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, dict): rows += [indent + key + ' {', blk(v, indent + '  '), indent + '}']
            elif isinstance(v, bool): rows.append(indent + key + ':b = ' + ('yes' if v else 'no'))
            elif isinstance(v, (int, float)): rows.append(indent + key + ':r = ' + str(v))
            else: rows.append(indent + key + ':t = "' + v + '"')
    return '\n'.join(rows) + '\n'


class AmmunitionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.lang = self.root / 'lang'; self.lang.mkdir()
        self.localization({'belt/name': '.50 API-T', 'mixed/name': 'Mixed', 'round_m20': 'M20'})
        self.bullet = {'bulletName': 'round_m20', 'bulletType': 'ap_i_t_ball_M20',
            'caliber': .0127, 'mass': .04, 'speed': 900., 'slopeEffectPreset': 'ap', 'ricochetPreset': 'default',
            'damage': {'kinetic': {'damageType': 'generic'}}}
        self.unit = {'commonWeapons': {'Weapon': [{'blk': 'gamedata/weapons/gun.blk', 'trigger': 'gunner0'},
            {'blk': 'gamedata/weapons/gun.blk', 'trigger': 'gunner1'}]},
            'modifications': {'belt': {}, 'mixed': {}}}
        self.weapon = {'isBulletBelt': True, 'bullet': self.bullet, 'belt': {'bullet': self.bullet},
            'mixed': {'bullet': [self.bullet, {**self.bullet, 'bulletName': 'other', 'mass': .06}]}}
        self.put('gamedata/units/tankmodels/vehicle.blk', self.unit)
        self.put('gamedata/weapons/gun.blk', self.weapon)
        self.ammo = {'id': 'vehicle::API-T', 'name': '.50 API-T', 'caliber_mm': 12.7,
            'weapon_id': 'vehicle::weapon:0', 'provenance': {'parent_weapon_name': 'Test gun'},
            'penetration': [{'distance_m': 10., 'angle_deg': 0., 'mm': 29.},
                            {'distance_m': 100., 'angle_deg': 0., 'mm': 27.}]}
        self.tables = {('Test gun', '.50 API-T'): {'points': self.ammo['penetration'], 'maximum_only': True}}
        self.response = {'slope': {'ap': {'table': {'caliberToArmor': 1., 'slopeEffect0': [0., 4.], 'slopeEffect90': [90., 1.]}}},
            'ricochet': {'default': {'table': {'caliberToArmor': 1., 'ricochetProbability0': [0., 1.], 'ricochetProbability90': [90., 0.]}}},
            'dispersion': .1, 'hashes': {}}
        self.source = {'sha256': 'a' * 64, 'url': 'https://wiki.warthunder.com/unit/vehicle'}

    def tearDown(self): self.temp.cleanup()
    def put(self, name, data):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(blk(data), encoding='utf-8')
    def localization(self, values):
        for name in ('units_modifications.csv', 'units_weaponry.csv'):
            with (self.lang / name).open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream, delimiter=';'); writer.writerow(['<ID|readonly|noverify>', '<English>'])
                if name == 'units_modifications.csv': writer.writerows(values.items())
    def adapter(self): return MountedAmmunition(self.root, Localization(self.lang))
    def binding(self):
        adapter = self.adapter()
        return adapter.bind(adapter.candidates({'id': 'vehicle'}), self.ammo)
    def profile(self, binding=None, ammo=None, tables=None):
        return ke_profile(ammo or self.ammo, binding or self.binding(), tables or self.tables,
            self.source, self.response, 'test', 'a' * 40)

    def test_homogeneous_maximum_table_has_exact_source_and_owned_profile(self):
        p = self.profile(); self.assertEqual(p['normalized_distance_curve'], [[10., 1.], [100., 27 / 29]])
        self.assertEqual(p['penetration_mm'], 29); self.assertEqual(p['damage_caliber_mm'], 12.7)
        self.assertEqual(p['provenance']['penetration_source_ammo_ids'], ['vehicle::API-T'])
        evidence = p['provenance']['recovery_bindings']['vehicle::API-T']
        self.assertEqual(evidence['selector'], 'belt'); self.assertEqual(len(evidence['mounts']), 2)
        self.assertEqual(evidence['ordered_projectiles'], ['round_m20'])
        self.assertTrue(evidence['wiki_maximum_only']); self.assertIn('damage_caliber_mm', p['provenance']['model_assumptions'])

    def test_mixed_belt_maximum_is_never_used_as_each_round(self):
        adapter = self.adapter(); mixed = {**self.ammo, 'name': 'Mixed'}
        binding = adapter.bind(adapter.candidates({'id': 'vehicle'}), mixed)
        self.assertEqual([b['bulletName'] for b in binding['bullets']], ['round_m20', 'other'])
        with self.assertRaisesRegex(ValueError, 'mixed_belt'): self.profile(binding=binding)

    def test_primary_modification_weapon_is_source_bound_and_not_baseline(self):
        self.localization({'round_m20':'M20','rocket_round':'Rocket'})
        rocket={'bulletName':'rocket_round','bulletType':'heat_tank','caliber':.07}
        self.put('gamedata/weapons/rocket.blk',{'bullet':rocket})
        self.unit['modifications']['upgrade']={'effects':{'commonWeapons':{'Weapon':[
            {'blk':'gamedata/weapons/gun.blk','trigger':'gunner0'},
            {'blk':'gamedata/weapons/rocket.blk','trigger':'gunner2'}]}}}
        self.put('gamedata/units/tankmodels/vehicle.blk',self.unit)
        ammo={**self.ammo,'id':'vehicle::rocket','name':'Rocket','caliber_mm':70.}
        with self.assertRaisesRegex(ValueError,'selection_missing'):
            self.adapter().bind(self.adapter().candidates({'id':'vehicle'}),ammo)
        adapter=MountedAmmunition(self.root,Localization(self.lang),primary_rules={'source_version':'test'})
        binding=adapter.bind(adapter.candidates({'id':'vehicle'}),ammo)
        self.assertEqual(binding['primary_modification_id'],'upgrade')
        self.assertEqual(binding['required_modification_ids'],['upgrade'])
        self.assertEqual(binding['mounts'],[{'trigger':'gunner2'}])
        self.assertEqual(binding['primary_armament_rules']['source_version'],'test')

    def test_root_rocket_requires_exact_full_and_short_weapon_localization(self):
        self.localization({'rocket_round':'Other label','weapons/rocket':'Rocket launcher','weapons/rocket/short':'Rocket'})
        rocket={'bulletName':'rocket_round','bulletType':'atgm_tank','caliber':.127}
        self.put('gamedata/weapons/rocket.blk',{'rocketGun':True,'rocket':rocket})
        self.unit['commonWeapons']={'Weapon':{'blk':'gamedata/weapons/rocket.blk','trigger':'gunner0'}}
        self.put('gamedata/units/tankmodels/vehicle.blk',self.unit)
        ammo={**self.ammo,'id':'vehicle::rocket','name':'Rocket','caliber_mm':127.,
              'provenance':{'parent_weapon_name':'Rocket launcher'}}
        with self.assertRaisesRegex(ValueError,'requires verified'):
            MountedAmmunition(self.root,Localization(self.lang),root_rockets=True)
        rules={'source_version':'test'}
        adapter=MountedAmmunition(self.root,Localization(self.lang),default_rules=rules,root_rockets=True,structural_projectiles=True)
        binding=adapter.bind(adapter.candidates({'id':'vehicle'}),ammo)
        self.assertEqual(binding['projectile_block'],'root_rocket');self.assertEqual(binding['bullets'],[rocket])
        self.assertEqual(binding['root_rocket_rules'],rules)
        self.assertEqual([v['value'] for v in binding['localization']],['Rocket launcher','Rocket'])
        for bad in ({**ammo,'name':'Other label'}, {**ammo,'provenance':{'parent_weapon_name':'Different launcher'}}):
            with self.subTest(bad=bad),self.assertRaisesRegex(ValueError,'selection_missing'):
                adapter.bind(adapter.candidates({'id':'vehicle'}),bad)

    def test_root_rocket_cannot_mask_ambiguous_projectile_blocks(self):
        from wt_atlas.ammunition_recovery import selected_projectiles
        rocket={'bulletName':'rocket','bulletType':'atgm_tank'}
        for weapon in ({'rocketGun':False,'rocket':rocket},{'rocketGun':True,'rocket':[rocket,rocket]},
                       {'rocketGun':True,'rocket':rocket,'bullet':rocket}):
            with self.subTest(weapon=weapon),self.assertRaisesRegex(ValueError,'shape changed'):
                selected_projectiles(weapon,'@default','root_rocket')

    def test_two_alternative_primary_modifications_are_not_silently_merged(self):
        self.localization({'rocket_round':'Rocket'})
        self.put('gamedata/weapons/rocket.blk',{'bullet':{'bulletName':'rocket_round','bulletType':'heat_tank'}})
        effect={'effects':{'commonWeapons':{'Weapon':{'blk':'gamedata/weapons/rocket.blk'}}}}
        self.unit['modifications'].update(first=effect,second=effect)
        self.put('gamedata/units/tankmodels/vehicle.blk',self.unit)
        adapter=MountedAmmunition(self.root,Localization(self.lang),primary_rules={'source_version':'test'})
        with self.assertRaisesRegex(ValueError,'ambiguous_localized'):
            adapter.bind(adapter.candidates({'id':'vehicle'}),{**self.ammo,'name':'Rocket'})

    def test_primary_weapon_slot_prerequisites_are_not_dropped(self):
        self.localization({'rocket_round':'Rocket'})
        self.put('gamedata/weapons/rocket.blk',{'bullet':{'bulletName':'rocket_round','bulletType':'heat_tank'}})
        self.unit['WeaponSlots']={'WeaponSlot':{'index':1,'WeaponPreset':{'name':'mount',
            'reqModification':'mount_unlock','Weapon':{'blk':'gamedata/weapons/rocket.blk','trigger':'gunner2'}}}}
        self.unit['modifications'].update(mount_unlock={},upgrade={'effects':{'commonWeapons':{
            'Weapon':{'slot':1,'preset':'mount'}}}})
        self.put('gamedata/units/tankmodels/vehicle.blk',self.unit)
        adapter=MountedAmmunition(self.root,Localization(self.lang),primary_rules={'source_version':'test'})
        binding=adapter.bind(adapter.candidates({'id':'vehicle'}),{**self.ammo,'name':'Rocket'})
        self.assertEqual(binding['required_modification_ids'],['mount_unlock','upgrade'])

    def test_conditional_ownership_does_not_mutate_existing_profile_identity(self):
        base=self.profile();conditional=copy.deepcopy(base)
        proof=conditional['provenance']['recovery_bindings'].pop('vehicle::API-T')
        proof.update(primary_modification_id='upgrade',required_modification_ids=['upgrade'])
        conditional['provenance']['recovery_bindings']={'vehicle::conditional':proof}
        conditional['provenance']['penetration_source_ammo_ids']=['vehicle::conditional']
        previous,_=consolidate([base]);combined,_=consolidate([base,conditional])
        self.assertEqual(len(combined),2)
        self.assertIn(previous[0],combined)

    def test_root_rocket_source_does_not_mutate_existing_profile_identity(self):
        base=self.profile();rocket=copy.deepcopy(base)
        proof=rocket['provenance']['recovery_bindings'].pop('vehicle::API-T')
        proof['projectile_block']='root_rocket'
        rocket['provenance']['recovery_bindings']={'vehicle::rocket':proof}
        rocket['provenance']['penetration_source_ammo_ids']=['vehicle::rocket']
        previous,_=consolidate([base]);combined,_=consolidate([base,rocket])
        self.assertEqual(len(combined),2);self.assertIn(previous[0],combined)

    def test_component_index_rechecks_the_complete_raw_projectile(self):
        from wt_atlas.ordered_belt_recovery import component_index
        from wt_atlas.common import content_hash
        p = self.profile(); adapter = self.adapter()
        index = component_index([p], adapter)
        self.assertEqual(list(index), [content_hash(self.bullet)])
        forged = copy.deepcopy(p)
        next(iter(forged['provenance']['recovery_bindings'].values()))['projectile_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'raw projectile source'):
            component_index([forged], adapter)
        self.weapon['belt']['bullet'] = {**self.bullet,'speed':1200.}
        self.put('gamedata/weapons/gun.blk',self.weapon)
        with self.assertRaisesRegex(ValueError, 'raw projectile source'):
            component_index([p], self.adapter())

    def test_ordered_reuse_rejects_missing_component_and_unequal_counts(self):
        from wt_atlas.ordered_belt_recovery import recover_belt
        binding = self.binding(); first = {**self.bullet,'bulletCount':2}
        second = {**first,'bulletName':'other','mass':.06}
        binding['bullets'] = [first,second]
        before = copy.deepcopy(self.ammo)
        with self.assertRaisesRegex(ValueError, 'component_profile_missing'):
            recover_belt({'id':'vehicle'},self.ammo,binding,{},'test','a'*40,self.source)
        self.assertEqual(self.ammo,before)
        second['bulletCount'] = 1
        with self.assertRaisesRegex(ValueError, 'unequal_component_counts'):
            recover_belt({'id':'vehicle'},self.ammo,binding,{},'test','a'*40,self.source)
        self.assertEqual(self.ammo,before)

    def test_incremental_belt_receipt_is_recreated_not_trusted(self):
        from wt_atlas.ordered_belt_recovery import component_index, recover_belt, verify_existing_belts
        self.bullet['bulletCount'] = 2; self.put('gamedata/weapons/gun.blk',self.weapon)
        path = self.root/'gamedata/weapons/gun.blk'
        path.write_text(path.read_text().replace('bulletCount:r','bulletCount:i'))
        profile = self.profile(); adapter = self.adapter(); components = component_index([profile],adapter)
        vehicle = {'id':'vehicle','ammo':[self.ammo]}
        recover_belt(vehicle,self.ammo,self.binding(),components,'test','a'*40,self.source)
        with patch('wt_atlas.ammunition_recovery.verify_wiki',return_value=({},self.source)):
            verify_existing_belts({'vehicles':[vehicle]},components,adapter,self.root,'test','a'*40)
            self.ammo['ordered_belt']['projectiles'][0]['profile_sha256'] = '0'*64
            with self.assertRaisesRegex(ValueError,'Existing ordered belt source changed'):
                verify_existing_belts({'vehicles':[vehicle]},components,adapter,self.root,'test','a'*40)

    def test_matching_component_name_cannot_select_a_mixed_belt(self):
        self.weapon.pop('bullet'); self.weapon.pop('belt'); self.put('gamedata/weapons/gun.blk', self.weapon)
        adapter = self.adapter()
        with self.assertRaisesRegex(ValueError, 'selection_missing'):
            adapter.bind(adapter.candidates({'id': 'vehicle'}), {**self.ammo, 'name': 'M20'})

    def selector_adapter(self, effects):
        self.put('modifications.blk', {'modifications': effects})
        rules={'selectors':selector_effects(parse_ordered_blk(self.root/'modifications.blk')),
               'evidence':{'source_version':'test','modifications_sha256':file_hash(self.root/'modifications.blk')}}
        return MountedAmmunition(self.root,Localization(self.lang),selector_rules=rules)

    def test_declared_modification_resolves_exact_different_weapon_selector(self):
        self.weapon['real-selector']=self.weapon.pop('belt');self.put('gamedata/weapons/gun.blk',self.weapon)
        a=self.selector_adapter({'belt':{'effects':{'additiveBulletMod':'real-selector'}}})
        bound=a.bind(a.candidates({'id':'vehicle'}),self.ammo)
        self.assertEqual(bound['selector'],'real-selector');self.assertEqual(bound['modification_id'],'belt')
        self.assertIn('selector_rules',bound)
        self.assertEqual(self.profile(binding=bound)['penetration_mm'],29)

    def test_effect_source_precedence_is_ordered_not_alphabetical(self):
        a=self.selector_adapter({'belt':{'effects':{'bulletMod':'chosen','additiveBulletMod':'wrong'}}})
        self.assertEqual(a.selector_rules['selectors']['belt'],{'kind':'bulletMod','selector':'chosen'})
        a=self.selector_adapter({'belt':{'effects':{'additiveBulletMod':'chosen','bulletMod':'wrong'}}})
        self.assertEqual(a.selector_rules['selectors']['belt']['selector'],'chosen')

    def test_wrong_or_undeclared_effect_does_not_fall_back_to_equal_named_block(self):
        for effects in ({'belt':{'effects':{'bulletMod':'absent'}}},{'unmounted':{'effects':{'bulletMod':'belt'}}}):
            a=self.selector_adapter(effects)
            self.assertEqual([x['selector'] for x in a.candidates({'id':'vehicle'})],['@default'])

    def test_verified_cannon_label_identifies_whole_mixed_belt_without_maximum_profile(self):
        self.weapon.pop('bullet');self.weapon.pop('belt')
        for b in self.weapon['mixed']['bullet']:b['caliber']=.035
        self.put('gamedata/weapons/gun.blk',self.weapon)
        a=self.selector_adapter({'mixed':{'effects':{'bulletMod':'mixed'}}})
        bound=a.bind(a.candidates({'id':'vehicle'}),{**self.ammo,'name':'M20'})
        self.assertEqual([b['bulletName'] for b in bound['bullets']],['round_m20','other'])
        self.assertIn('complete_ordered_belt_retained',bound['display_name_rule'])
        with self.assertRaisesRegex(ValueError,'mixed_belt'):self.profile(binding=bound)

    def test_cannon_label_never_selects_a_later_belt_member_or_small_machinegun(self):
        self.weapon.pop('bullet');self.weapon.pop('belt')
        self.localization({'round_m20':'M20','other':'Other'})
        for caliber,name in ((.0127,'M20'),(.035,'Other')):
            for b in self.weapon['mixed']['bullet']:b['caliber']=caliber
            self.put('gamedata/weapons/gun.blk',self.weapon)
            a=self.selector_adapter({'mixed':{'effects':{'bulletMod':'mixed'}}})
            with self.assertRaisesRegex(ValueError,'selection_missing'):
                a.bind(a.candidates({'id':'vehicle'}),{**self.ammo,'name':name})

    def test_cannon_localization_fallback_obeys_existing_direct_label(self):
        self.weapon.pop('bullet');self.weapon.pop('belt')
        for b in self.weapon['mixed']['bullet']:b['caliber']=.035
        self.put('gamedata/weapons/gun.blk',self.weapon)
        self.localization({'weapons/round_m20/short':'Fallback'})
        a=self.selector_adapter({'mixed':{'effects':{'bulletMod':'mixed'}}})
        self.assertEqual(a.bind(a.candidates({'id':'vehicle'}),{**self.ammo,'name':'Fallback'})['localization'][0]['key'],'weapons/round_m20/short')
        self.localization({'round_m20':'Primary','weapons/round_m20/short':'Fallback'})
        a=self.selector_adapter({'mixed':{'effects':{'bulletMod':'mixed'}}})
        with self.assertRaisesRegex(ValueError,'selection_missing'):
            a.bind(a.candidates({'id':'vehicle'}),{**self.ammo,'name':'Fallback'})

    def test_selector_capture_checks_scripts_registry_and_versions(self):
        self.put('config/modifications.blk',{'modifications':{'belt':{'effects':{'bulletMod':'belt'}}}})
        (self.root/'script.nut').write_bytes(b'reviewed\r\n');(self.root/'pack').write_bytes(b'pack')
        pack={'version':'test','saved':str(self.root/'pack'),'sha256':file_hash(self.root/'pack'),'extracted':str(self.root)}
        capture={'packs':{'char.vromfs.bin':pack,'gui.vromfs.bin':copy.deepcopy(pack)}}
        with patch('wt_atlas.ammunition_selectors.REVIEWED_SCRIPTS',{'script.nut':hashlib.sha256(b'reviewed\n').hexdigest()}):
            first=verified_selectors(capture,'test');self.assertEqual(first['selectors']['belt']['selector'],'belt')
            self.put('config/modifications.blk',{'modifications':{'belt':{'effects':{'bulletMod':'changed'}}}})
            self.assertNotEqual(first['evidence'],verified_selectors(capture,'test')['evidence'])
            with self.assertRaisesRegex(ValueError,'capture mismatch'):verified_selectors(capture,'other')
            (self.root/'script.nut').write_bytes(b'unreviewed')
            with self.assertRaisesRegex(ValueError,'Unreviewed'):verified_selectors(capture,'test')

    def test_unmounted_and_undeclared_selectors_do_not_bind(self):
        self.unit['modifications'].pop('belt'); self.put('gamedata/units/tankmodels/vehicle.blk', self.unit)
        adapter = self.adapter()
        with self.assertRaisesRegex(ValueError, 'selection_missing'):
            adapter.bind(adapter.candidates({'id': 'vehicle'}), self.ammo)

    def test_same_display_name_on_different_mounted_weapons_is_ambiguous(self):
        self.put('gamedata/weapons/other.blk', self.weapon)
        self.unit['commonWeapons']['Weapon'].append({'blk': 'gamedata/weapons/other.blk'})
        self.put('gamedata/units/tankmodels/vehicle.blk', self.unit); adapter = self.adapter()
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            adapter.bind(adapter.candidates({'id': 'vehicle'}), self.ammo)

    def test_same_projectile_in_default_and_named_selector_is_equivalent(self):
        adapter = self.adapter()
        match = adapter.bind(adapter.candidates({'id': 'vehicle'}), {**self.ammo, 'name': 'M20'})
        self.assertEqual(match['equivalent_selectors'], ['@default', 'belt'])

    def test_repeated_source_identity_is_reported_without_aborting_other_selections(self):
        self.weapon['belt']['bullet'] = {**self.bullet, 'bulletName': ['one', 'two']}
        self.put('gamedata/weapons/gun.blk', self.weapon); adapter = self.adapter()
        candidates = adapter.candidates({'id': 'vehicle'})
        self.assertTrue(candidates)
        self.assertEqual(adapter.rejections[0]['reason'], 'projectile_identity_missing_or_repeated')
        with self.assertRaisesRegex(ValueError, 'selection_missing'): adapter.bind(candidates, self.ammo)

    def test_no_fuzzy_case_or_caliber_name_matching(self):
        adapter = self.adapter()
        with self.assertRaisesRegex(ValueError, 'selection_missing'):
            adapter.bind(adapter.candidates({'id': 'vehicle'}), {**self.ammo, 'name': '.50 api-t'})
        with self.assertRaisesRegex(ValueError, 'caliber_mismatch'):
            self.profile(ammo={**self.ammo, 'caliber_mm': 20.})

    def unnamed_weapon(self):
        raw={k:v for k,v in self.bullet.items() if k!='bulletName'}
        self.weapon={'isBulletBelt':True,'bullet':raw,'belt':{'bullet':raw}}
        self.put('gamedata/weapons/gun.blk',self.weapon)
        return raw

    def test_unnamed_projectile_has_exact_source_identity_without_fabricated_name(self):
        raw=self.unnamed_weapon()
        adapter=MountedAmmunition(self.root,Localization(self.lang),structural_projectiles=True)
        binding=adapter.bind(adapter.candidates({'id':'vehicle'}),self.ammo)
        self.assertEqual(binding['bullets'],[raw]);self.assertNotIn('bulletName',binding['bullets'][0])
        identity=binding['projectile_identities'][0]
        self.assertIsNone(identity['declared_bullet_name'])
        self.assertEqual(identity['selector'],'belt');self.assertEqual(identity['bullet_index'],0)
        self.assertEqual(identity['source_weapon_sha256'],file_hash(self.root/'gamedata/weapons/gun.blk'))
        profile=self.profile(binding=binding)
        self.assertIsNone(profile['provenance']['bullet_name'])
        proof=profile['provenance']['recovery_bindings'][self.ammo['id']]
        self.assertEqual(proof['projectile_identities'],binding['projectile_identities'])
        self.assertEqual(proof['ordered_projectiles'],[None])
        with self.assertRaisesRegex(ValueError,'selection_missing'):
            self.adapter().bind(self.adapter().candidates({'id':'vehicle'}),self.ammo)

    def test_structural_identity_distinguishes_position_source_selector_and_content(self):
        from wt_atlas.projectile_identity import identities
        raw=self.unnamed_weapon();first=identities('gun.blk','a'*64,'@default',[raw,raw])
        self.assertNotEqual(first[0]['id'],first[1]['id'])
        for path,sha,selector,bullet in [('other.blk','a'*64,'@default',raw),('gun.blk','b'*64,'@default',raw),
            ('gun.blk','a'*64,'belt',raw),('gun.blk','a'*64,'@default',{**raw,'mass':.06})]:
            self.assertNotEqual(first[0]['id'],identities(path,sha,selector,[bullet])[0]['id'])
        self.assertEqual(first,identities('gun.blk','a'*64,'@default',[raw,raw]))

    def test_unnamed_default_mixture_is_bound_but_maximum_table_remains_unusable(self):
        raw=self.unnamed_weapon();self.weapon['bullet']=[raw,{**raw,'mass':.09}]
        self.put('gamedata/weapons/gun.blk',self.weapon)
        adapter=self.default_adapter();adapter.structural_projectiles=True
        binding=adapter.bind(adapter.candidates({'id':'vehicle'}),self.default_ammo())
        self.assertEqual(len(binding['projectile_identities']),2)
        self.assertEqual(binding['bullets'],self.weapon['bullet'])
        with self.assertRaisesRegex(ValueError,'mixed_belt_requires_individual_projectile_tables'):
            self.profile(binding=binding,ammo=self.default_ammo())

    def test_structural_mode_still_rejects_conflicting_declared_names(self):
        self.weapon['belt']['bullet']={**self.bullet,'bulletName':['one','two']}
        self.put('gamedata/weapons/gun.blk',self.weapon)
        adapter=MountedAmmunition(self.root,Localization(self.lang),structural_projectiles=True)
        with self.assertRaisesRegex(ValueError,'selection_missing'):
            adapter.bind(adapter.candidates({'id':'vehicle'}),self.ammo)
        self.assertEqual(len(adapter.rejections),1)

    def test_unnamed_recovery_replay_checks_source_and_structural_proof(self):
        self.unnamed_weapon();args=self.integration_inputs()
        catalog,profiles,report=recover(*args,structural_projectiles=True)
        self.assertEqual(report['counts']['new_profiles'],1)
        write_json(args[0],catalog);write_json(args[1],profiles)
        _,again,replay=recover(*args,structural_projectiles=True)
        self.assertEqual(again,profiles);self.assertEqual(replay['counts']['new_profiles'],0)
        with self.assertRaisesRegex(ValueError,'structural projectile evidence was not enabled'):recover(*args)
        changed=copy.deepcopy(profiles)
        changed[0]['provenance']['recovery_bindings'][self.ammo['id']]['projectile_identities'][0]['bullet_index']=1
        write_json(args[1],changed)
        with self.assertRaisesRegex(ValueError,'structural projectile evidence changed'):recover(*args,structural_projectiles=True)
        write_json(args[1],profiles)
        self.weapon['belt']['bullet']['speed']=901.;self.put('gamedata/weapons/gun.blk',self.weapon)
        with self.assertRaisesRegex(ValueError,'structural projectile evidence changed'):recover(*args,structural_projectiles=True)

    def test_subcaliber_requires_explicit_damage_caliber(self):
        binding = self.binding(); binding['bullets'][0] = {**self.bullet, 'bulletType': 'apds_tank'}
        with self.assertRaisesRegex(ValueError, 'damage_caliber_missing'): self.profile(binding=binding)
        binding['bullets'][0]['damageCaliber'] = .008
        self.assertEqual(self.profile(binding=binding)['damage_caliber_mm'], 8.)

    def test_unverified_damage_mechanism_does_not_default_to_generic(self):
        binding = self.binding(); binding['bullets'][0] = {**self.bullet, 'damage': {}}
        with self.assertRaisesRegex(ValueError, 'mechanism_not_verified'): self.profile(binding=binding)

    def test_catalog_table_change_is_rejected(self):
        ammo = copy.deepcopy(self.ammo); ammo['penetration'][0]['mm'] = 999.
        with self.assertRaisesRegex(ValueError, 'differs_from_exact'): self.profile(ammo=ammo)

    def test_consolidation_preserves_every_mount_proof_and_distinguishes_source_projectiles(self):
        a = self.profile(); b = self.profile(ammo={**self.ammo, 'id': 'other::API-T'})
        merged, renamed = consolidate([a, b]); self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]['provenance']['recovery_bindings']), 2)
        self.assertEqual(renamed[a['name']], renamed[b['name']])
        b['provenance']['recovery_bindings']['other::API-T']['projectile_sha256'] = '0' * 64
        self.assertEqual(len(consolidate([a, b])[0]), 2)

    def test_incremental_new_ownership_merges_without_duplicate_physical_profiles(self):
        old, _ = consolidate([self.profile()]); before = copy.deepcopy(old)
        new, _ = consolidate([self.profile(ammo={**self.ammo, 'id': 'other::default'})])
        merged = merge_recovered(old, new)
        self.assertEqual(len(merged), 1); self.assertEqual(old, before)
        self.assertEqual(set(merged[0]['provenance']['recovery_bindings']), {self.ammo['id'], 'other::default'})
        self.assertEqual(merge_recovered(merged, new), merged)
        new[0]['penetration_mm'] += 1
        with self.assertRaisesRegex(ValueError, 'conflicts with existing'): merge_recovered(old, new)

    def test_source_and_localization_conflicts_are_rejected(self):
        adapter = self.adapter(); adapter.candidates({'id': 'vehicle'})
        self.put('gamedata/weapons/gun.blk', {'bullet': self.bullet})
        with self.assertRaisesRegex(ValueError, 'changed during'): adapter.verify_unchanged()
        with (self.lang / 'units_weaponry.csv').open('a', encoding='utf-8') as stream: stream.write('belt/name;different\n')
        with self.assertRaisesRegex(ValueError, 'Conflicting exact'): Localization(self.lang)

    def test_response_points_must_be_valid_and_nonconflicting(self):
        table = self.response['slope']['ap']['table']; table['slopeEffectDuplicate'] = [90., 2.]
        with self.assertRaisesRegex(ValueError, 'conflicting_native'): source_rows(self.response['slope'], 'ap', 'slopeEffect', True)

    def raw_wiki(self):
        table = '<div>Armor penetration (max.)<table><tr><th>Distance / Angle</th><th>0</th></tr><tr><th>10 m</th><td>29</td></tr><tr><th>100 m</th><td>27</td></tr></table></div>'
        pop = '<div class="game-unit_popover-header"><span>.50 API-T</span></div><button data-bs-title="Armor penetration" data-bs-content="' + escape(table, quote=True) + '"></button>'
        return ('<div class="game-unit_weapon"><div class="game-unit_weapon-title">Test gun</div><button data-feature-popover="' + escape(pop, quote=True) + '"></button></div>').encode()

    def test_wiki_table_keeps_parent_weapon_and_maximum_semantics(self):
        self.assertEqual(wiki_tables(self.raw_wiki()), self.tables)

    def fixture_catalog(self):
        raw = self.raw_wiki(); (self.root / 'unit_vehicle.html.gz').write_bytes(gzip.compress(raw))
        url = self.source['url']
        vehicle = {'id': 'vehicle', 'kind': 'ground', 'wiki_url': url,
            'provenance': [{'url': url, 'sha256': hashlib.sha256(raw).hexdigest()}],
            'ammo': [self.ammo], 'weapon_profiles': [{'id': 'vehicle::weapon:0', 'reload_type': 'automatic', 'fire_rate_rpm': 575.}]}
        return {'snapshot': {'client_source': {'version': 'test'}, 'datamine_game_version': 'test', 'datamine_reference': 'a' * 40}, 'vehicles': [vehicle]}

    def test_wrong_wiki_hash_fails(self):
        vehicle = self.fixture_catalog()['vehicles'][0]; vehicle['provenance'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Wiki source hash'): verify_wiki(vehicle, self.root)

    def integration_inputs(self):
        catalog = self.fixture_catalog(); write_json(self.root / 'catalog.json', catalog); write_json(self.root / 'ammo.json', [])
        for name, data in [('slope_effect', self.response['slope']), ('ricochet', self.response['ricochet'])]:
            data = copy.deepcopy(data)
            for preset in data.values():
                for table in preset.values():
                    for k, v in table.items():
                        if isinstance(v, list): table[k] = tuple(v)
            self.put('gamedata/damage_model/' + name + '.blk', data)
        self.put('gamedata/damage_model/damage_system.blk', {'pierceDispersion': .1})
        (self.root / 'pack').write_bytes(b'fixture')
        write_json(self.root / 'capture.json', {'packs': {'aces.vromfs.bin': {'version': 'test', 'saved': str(self.root / 'pack'), 'sha256': file_hash(self.root / 'pack'), 'extracted': str(self.root)}}})
        write_json(self.root / 'language.json', {'version': 'test', 'saved': str(self.root / 'pack'), 'source_sha256': file_hash(self.root / 'pack'), 'extracted': str(self.root)})
        from wt_atlas.forecast.rule_snapshot import SOURCE_PATHS
        rules = self.root / 'rules'; files = {}
        for path in SOURCE_PATHS:
            target = rules / path; target.parent.mkdir(parents=True, exist_ok=True)
            data = b'test' if path == 'version' else b'{}'; target.write_bytes(data)
            files[path] = {'url': 'https://raw.githubusercontent.com/gszabi99/War-Thunder-Datamine/' + 'a' * 40 + '/' + path,
                'sha256': file_hash(target), 'git_blob_sha': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}
        write_json(rules / 'capture.json', {'commit': 'a' * 40, 'files': files})
        return [self.root / n for n in ('catalog.json', 'ammo.json', 'capture.json', 'language.json')] + [self.root, rules]

    def test_recovery_pipeline_preserves_inputs_and_restores_bound_weapon_timing(self):
        args = self.integration_inputs(); before = [file_hash(p) for p in args[:4]]
        catalog, profiles, report = recover(*args)
        self.assertEqual(report['counts']['recovered_profiles'], 1); self.assertEqual(len(profiles), 1)
        self.assertEqual(catalog['vehicles'][0]['ammo'][0]['weapon_profile']['fire_rate_rpm'], 575.)
        self.assertEqual(before, [file_hash(p) for p in args[:4]])
        self.assertEqual(report['computed_maps'], 0); self.assertEqual(report['formal_rankings'], 0)

    def test_version_mismatch_and_pack_hash_failure_reject_before_import(self):
        args = self.integration_inputs(); (self.root / 'pack').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'): recover(*args)
        args = self.integration_inputs(); write_json(args[3], {'version': 'other'})
        with self.assertRaisesRegex(ValueError, 'share a version'): recover(*args)

    def test_incremental_import_reuses_verified_profiles_and_rejects_changed_sources(self):
        args = self.integration_inputs(); catalog, profiles, _ = recover(*args)
        write_json(args[0], catalog); write_json(args[1], profiles)
        _, again, report = recover(*args)
        self.assertEqual(again, profiles); self.assertEqual(report['counts']['recovered_profiles'], 0)
        self.weapon['belt']['bullet'] = {**self.bullet, 'mass': .09}
        self.put('gamedata/weapons/gun.blk', self.weapon)
        with self.assertRaisesRegex(ValueError, 'Existing recovered client source changed'): recover(*args)

    def test_pinned_datamine_file_tampering_is_rejected(self):
        args = self.integration_inputs(); (args[-1] / 'version').write_text('other', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'checksum or URL mismatch'): recover(*args)

    def default_adapter(self):
        with (self.lang / 'menu_options.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream, delimiter=';'); writer.writerow(['<ID|readonly|noverify>', '<English>'])
            writer.writerow(['modification/default_bullets', 'Default'])
        with (self.lang / 'units_weaponry.csv').open('a', encoding='utf-8') as stream:
            stream.write('weapons/gun;Test gun\n')
        return MountedAmmunition(self.root, Localization(self.lang, include_defaults=True),
                                default_rules={'source_version': 'test', 'scripts': {}})

    def default_ammo(self):
        return {**self.ammo, 'name': 'Default', 'provenance': {'parent_weapon_name': '2 x Test gun'}}

    def test_default_belt_requires_exact_parent_weapon_and_mount_count(self):
        adapter = self.default_adapter(); candidates = adapter.candidates({'id': 'vehicle'})
        match = adapter.bind(candidates, self.default_ammo())
        self.assertEqual(match['selector'], '@default'); self.assertEqual(match['mounted_weapon_count'], 2)
        self.assertEqual([l['key'] for l in match['localization']], ['modification/default_bullets', 'weapons/gun'])
        for parent in ('Test gun', '3 x Test gun', '2 x test gun', '2 x Other gun'):
            ammo = {**self.default_ammo(), 'provenance': {'parent_weapon_name': parent}}
            with self.assertRaisesRegex(ValueError, 'selection_missing'): adapter.bind(candidates, ammo)

    def test_default_label_never_selects_named_or_hidden_ammunition(self):
        for field, value in [('notUseDefaultBulletInGui', True), ('useSingleIconForBullet', True), ('isBulletBelt', False)]:
            with self.subTest(field=field):
                weapon = {**self.weapon, field: value}; self.put('gamedata/weapons/gun.blk', weapon)
                adapter = self.default_adapter()
                with self.assertRaisesRegex(ValueError, 'selection_missing'):
                    adapter.bind(adapter.candidates({'id': 'vehicle'}), self.default_ammo())
        self.weapon.pop('bullet'); self.put('gamedata/weapons/gun.blk', self.weapon)
        adapter = self.default_adapter()
        with self.assertRaisesRegex(ValueError, 'selection_missing'):
            adapter.bind(adapter.candidates({'id': 'vehicle'}), self.default_ammo())

    def test_cartridge_belt_uses_gui_predicate_even_when_physical_belt_flag_is_false(self):
        self.weapon.update(isBulletBelt=False, bulletsCartridge=8)
        self.put('gamedata/weapons/gun.blk', self.weapon); adapter = self.default_adapter()
        binding = adapter.bind(adapter.candidates({'id': 'vehicle'}), self.default_ammo())
        self.assertTrue(binding['default_belt_eligible']); self.assertFalse(binding['is_belt'])

    def test_default_belt_never_uses_dummy_or_countermeasure_mounts(self):
        for field, value in [('dummy', True), ('trigger', 'countermeasures')]:
            unit = copy.deepcopy(self.unit)
            for mount in unit['commonWeapons']['Weapon']: mount[field] = value
            self.put('gamedata/units/tankmodels/vehicle.blk', unit); adapter = self.default_adapter()
            with self.assertRaisesRegex(ValueError, 'selection_missing'):
                adapter.bind(adapter.candidates({'id': 'vehicle'}), self.default_ammo())

    def test_default_belt_uses_case_preserved_source_weapon_basename(self):
        for mount in self.unit['commonWeapons']['Weapon']: mount['blk'] = 'gameData/Weapons/Gun.blk'
        self.put('gamedata/units/tankmodels/vehicle.blk', self.unit); adapter = self.default_adapter()
        with self.assertRaisesRegex(ValueError, 'selection_missing'):
            adapter.bind(adapter.candidates({'id': 'vehicle'}), self.default_ammo())
        with (self.lang / 'units_weaponry.csv').open('a', encoding='utf-8') as stream:
            stream.write('weapons/Gun;Test gun\n')
        adapter = self.default_adapter()
        self.assertEqual(adapter.bind(adapter.candidates({'id': 'vehicle'}), self.default_ammo())['weapon_localization_keys'], ['weapons/Gun'])

    def test_bound_default_mixture_still_requires_individual_projectile_tables(self):
        self.weapon['bullet'] = self.weapon['mixed']['bullet']; self.put('gamedata/weapons/gun.blk', self.weapon)
        adapter = self.default_adapter(); binding = adapter.bind(adapter.candidates({'id': 'vehicle'}), self.default_ammo())
        with self.assertRaisesRegex(ValueError, 'mixed_belt'): self.profile(binding=binding, ammo=self.default_ammo())

    def gui_capture(self):
        reviewed = {}
        for name in REVIEWED_SCRIPTS:
            path = self.root / 'gui' / name; path.parent.mkdir(parents=True, exist_ok=True)
            raw = b'synthetic test script\r\n'; path.write_bytes(raw)
            reviewed[name] = hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()
        pack = self.root / 'gui.pack'; pack.write_bytes(b'synthetic GUI pack')
        return {'version': 'test', 'saved': str(pack), 'sha256': file_hash(pack), 'extracted': str(self.root / 'gui')}, reviewed

    def test_default_gui_evidence_requires_pack_version_and_reviewed_script_hashes(self):
        pack, reviewed = self.gui_capture(); capture = {'packs': {'gui.vromfs.bin': pack}}
        with patch.dict(REVIEWED_SCRIPTS, reviewed, clear=True):
            rules = verified_rules(capture, 'test'); self.assertEqual(len(rules['scripts']), 4)
            with self.assertRaisesRegex(ValueError, 'version mismatch'): verified_rules(capture, 'other')
            script = self.root / 'gui' / next(iter(reviewed)); script.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'not reviewed'): verified_rules(capture, 'test')
            Path(pack['saved']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'): verified_rules(capture, 'test')

    def test_default_recovery_pipeline_and_incremental_gui_validation(self):
        args = self.integration_inputs(); self.default_adapter()
        catalog = read_json(args[0]); catalog['vehicles'][0]['ammo'] = [self.default_ammo()]
        raw = self.raw_wiki().replace(b'.50 API-T', b'Default').replace(b'Test gun', b'2 x Test gun')
        (self.root / 'unit_vehicle.html.gz').write_bytes(gzip.compress(raw))
        catalog['vehicles'][0]['provenance'][0]['sha256'] = hashlib.sha256(raw).hexdigest()
        write_json(args[0], catalog)
        capture = read_json(args[2]); pack, reviewed = self.gui_capture()
        capture['packs']['gui.vromfs.bin'] = pack; write_json(args[2], capture)
        with patch.dict(REVIEWED_SCRIPTS, reviewed, clear=True):
            out, profiles, report = recover(*args, default_belts=True)
            self.assertEqual(report['counts']['recovered_profiles'], 1)
            binding = profiles[0]['provenance']['recovery_bindings'][self.ammo['id']]
            self.assertEqual(binding['default_belt_rules'], report['default_belt_rules'])
            write_json(args[0], out); write_json(args[1], profiles)
            _, again, replay = recover(*args, default_belts=True)
            self.assertEqual(again, profiles); self.assertEqual(replay['counts']['recovered_profiles'], 0)
            with self.assertRaisesRegex(ValueError, 'GUI evidence changed or was not enabled'): recover(*args)
            path = self.root / 'gui' / next(iter(reviewed)); path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'not reviewed'): recover(*args, default_belts=True)


if __name__ == '__main__': unittest.main()
