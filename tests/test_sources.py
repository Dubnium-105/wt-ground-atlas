import copy
import json
from pathlib import Path
import unittest

from lxml import html
from wt_atlas.sources import (block_lines, mass_kg, parse_catalog,
    parse_popover_ammo, parse_vehicle_details)
from wt_atlas.tencent import decode_sheet, map_changes, normalized_name
from wt_atlas.loadouts import DatamineLoadouts

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests/fixtures'
CONFIG = json.loads((ROOT/'config/sources.json').read_text(encoding='utf-8'))


class SourcesRegressionTests(unittest.TestCase):
    def test_air_rb_is_never_used_as_ground_rb(self):
        row = ['fixture_aircraft','Fixture','usa',1,{'rb':30},0,[],[['fighter','Fighter',''],[],'']]
        source = "<script>window.WT_UnitList = '" + json.dumps([row]) + "';</script>"
        v = parse_catalog(source, 'aircraft', CONFIG['wiki'], 'fixture://wiki')[0]
        self.assertIsNone(v['br']['current'])
        self.assertIn('br.ground_realistic', v['missing'])
        self.assertIsNotNone(v['source_battle_ratings']['rb'])

    def test_heat_angle_table_does_not_turn_penetration_into_distance(self):
        f=json.loads((FIX/'wiki-ammo-popovers.json').read_text(encoding='utf-8'))['M830']
        a=parse_popover_ammo(f['html'],f['vehicle_id'])
        self.assertTrue(a['range_invariant'])
        self.assertEqual(a['penetration'],[
            {'distance_m':None,'angle_deg':0.0,'mm':480.0},
            {'distance_m':None,'angle_deg':30.0,'mm':416.0},
            {'distance_m':None,'angle_deg':60.0,'mm':240.0}])

    def test_apfsds_distance_table_retains_distance_and_angle(self):
        f=json.loads((FIX/'wiki-ammo-popovers.json').read_text(encoding='utf-8'))['DM33']
        a=parse_popover_ammo(f['html'],f['vehicle_id'])
        self.assertFalse(a['range_invariant'])
        self.assertIn({'distance_m':1000.0,'angle_deg':0.0,'mm':462.0},a['penetration'])
        self.assertIn({'distance_m':1000.0,'angle_deg':60.0,'mm':267.0},a['penetration'])

    def test_weapon_scope_cannot_inherit_machine_gun_rate_or_economy(self):
        source=(FIX/'wiki-weapons.html').read_text(encoding='utf-8')
        v={'id':'fixture','kind':'ground','role':'mbt','wiki_url':'fixture://wiki',
            'missing':[],'provenance':[],'loadouts':[]}
        parse_vehicle_details(source,v)
        main,mg=v['weapon_profiles'][:2]
        self.assertIsNone(main['fire_rate_rpm'])
        self.assertIsNone(main['belt_capacity'])
        self.assertNotIn('Economy sentinel',main['raw'])
        self.assertIsNotNone(mg['fire_rate_rpm'])
        self.assertEqual(main['reload_ace_s'],5.0)
        self.assertNotEqual(main['reload_ace_s'],mg['listed_reload_s'])

    def test_physical_units_are_converted_and_unknown_units_are_not_guessed(self):
        self.assertAlmostEqual(mass_kg('535 lbs'),242.67191795)
        self.assertAlmostEqual(mass_kg('907 g'),0.907)
        self.assertEqual(mass_kg('4.3 kg'),4.3)
        self.assertIsNone(mass_kg('1 mystery-unit'))

    def test_four_upward_only_rocket_mounts_do_not_gain_false_depression(self):
        fixtures=json.loads((FIX/'wiki-final-regressions.json').read_text(encoding='utf-8'))['rocket_guidance']
        self.assertEqual(len(fixtures),4)
        for fixture in fixtures:
            with self.subTest(vehicle_id=fixture['vehicle_id']):
                v={'id':fixture['vehicle_id'],'kind':'ground','role':'tank_destroyer',
                    'wiki_url':fixture['source_url'],'missing':[],'provenance':[],'loadouts':[]}
                parse_vehicle_details(fixture['html'],v)
                for profile in [v['firepower'],v['weapon_profiles'][0]]:
                    self.assertEqual(profile['min_elevation_deg'],fixture['expected_min_elevation_deg'])
                    self.assertEqual(profile['depression_deg'],-fixture['expected_min_elevation_deg'])
                    self.assertLess(profile['depression_deg'],0)
                    self.assertEqual(profile['elevation_deg'],fixture['expected_max_elevation_deg'])

    def test_air_maximum_speed_preserves_source_altitude_and_realistic_reference(self):
        fixtures=json.loads((FIX/'wiki-final-regressions.json').read_text(encoding='utf-8'))['air_speed']
        for fixture in fixtures:
            with self.subTest(vehicle_id=fixture['vehicle_id']):
                v={'id':fixture['vehicle_id'],'kind':'aircraft','role':'fighter',
                    'wiki_url':fixture['source_url'],'missing':[],'provenance':[],'loadouts':[]}
                parse_vehicle_details(fixture['html'],v)
                self.assertEqual(v['mobility']['forward_kmh'],fixture['expected_forward_kmh'])
                self.assertEqual(v['mobility']['speed_altitude_m'],fixture['expected_altitude_m'])

    def test_multi_vehicle_membership_does_not_invent_component_br_or_firepower(self):
        fixtures=json.loads((FIX/'wiki-final-regressions.json').read_text(encoding='utf-8'))['multiunit']
        for fixture in fixtures:
            with self.subTest(vehicle_id=fixture['vehicle_id']):
                v={'id':fixture['vehicle_id'],'kind':'ground','role':'spaa',
                    'wiki_url':fixture['source_url'],'br':{'current':None,'planned':None},
                    'missing':[],'provenance':[],'loadouts':[]}
                parse_vehicle_details(fixture['html'],v)
                self.assertEqual(v['system_id'],fixture['expected_basic_id'])
                members={m['vehicle_id']:m for m in v['multi_vehicle_system']['members']}
                self.assertEqual(members[fixture['expected_component_id']]['count'],fixture['expected_component_count'])
                self.assertEqual(members[fixture['expected_basic_id']]['role'],'basic')
                expected_role='basic' if v['id']==fixture['expected_basic_id'] else 'component'
                self.assertEqual(v['system_role'],expected_role)
                self.assertFalse(v['multi_vehicle_system']['capabilities_aggregated'])
                self.assertEqual(v['br'],{'current':None,'planned':None})
                self.assertIsNone(v['air_defence']['missile_range_m'])

    def test_tencent_public_binary_exact_numeric_pool_and_columns(self):
        rows=decode_sheet((FIX/'tencent-ground-block.bin').read_bytes(),CONFIG['changes']['numeric_inline_max'])
        self.assertEqual(rows['0']['3'],'历史性能')
        self.assertEqual(rows['2']['0'],'红旗11')
        self.assertEqual(rows['2']['3'],12.7)
        self.assertEqual(rows['2']['4'],13.3)

    def test_adjustment_conflicts_and_ambiguous_names_are_not_applied(self):
        cat={'vehicles':[{'id':'v1','name':'Shared','kind':'ground','nation':'usa','br':{'current':4.0}},
                         {'id':'v2','name':'Shared','kind':'ground','nation':'usa','br':{'current':4.0}},
                         {'id':'v3','name':'Unique','kind':'ground','nation':'usa','br':{'current':5.0}}]}
        aliases={normalized_name('Shared'):{'v1','v2'},normalized_name('Unique'):{'v3'}}
        rows={'0':{'3':'历史性能'},'1':{'3':'更新前','4':'更新后'},
              '2':{'0':'Shared','3':4.0,'4':4.3},'3':{'0':'Unique','3':4.0,'4':4.3}}
        accepted,audit=map_changes([{'id':'fixture','kind':'ground','rows':rows}],cat,aliases,CONFIG)
        self.assertEqual(accepted,[])
        self.assertEqual({a['status'] for a in audit},{'ambiguous','baseline_conflict'})

    def test_nested_container_counts_and_exact_weapon_identity(self):
        adapter=DatamineLoadouts(CONFIG,ROOT)
        resources={
            'rack.blk':{'container':True,'blk':'missile.blk','bullets':3},
            'missile.blk':{'rocketGun':True,'bullets':1,'rocket':{'bulletName':'exact_missile_A','guidanceType':'optical','mass':210.0}}
        }
        adapter.read=lambda path:resources[path]
        weapons=adapter.weapons({'blk':'rack.blk','trigger':'atgm'})
        self.assertEqual(weapons[0]['id'],'exact_missile_A')
        self.assertEqual(weapons[0]['count'],3)
        self.assertEqual(weapons[0]['type'],'agm')

    def test_cyclic_container_is_rejected(self):
        adapter=DatamineLoadouts(CONFIG,ROOT)
        adapter.read=lambda path:{'container':True,'blk':path,'bullets':1}
        with self.assertRaisesRegex(ValueError,'cyclic'):
            adapter.weapons({'blk':'cycle.blk','trigger':'atgm'})


if __name__=='__main__':unittest.main()
