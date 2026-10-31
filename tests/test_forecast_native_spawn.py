import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import write_json, file_hash
from wt_atlas.forecast.native_spawn import (interpolate, score_param, weapon_param,
    preset_parameters, weapon_multiplier, belt_multiplier, cost_components, import_components, script_digest)
from wt_atlas.forecast.rule_snapshot import WPCOST_PATH, RANKS_PATH


class NativeSpawnTests(unittest.TestCase):
    def setUp(self):
        self.rules = {'base_respawn_points_coef': 450, 'TankHistorical': {
            'spawn_pow': 1., 'useSpawnCostMulForWeapon': True, 'useSpawnCostMulForBullet': True,
            'respawn_cost_mul_by_exp_class': {'exp_fighter': 1.2, 'exp_helicopter': .7, 'reserve': .18}},
            'WeaponMul': {'Common': {
                'BombRocketWeapon': {'mass': [[200., 1.03], [2000., 1.37]]},
                'NapalmBombWeapon': {'mass': [[200., 1.03], [2000., 1.1]]},
                'GuidedBombWeapon': {'mass': [[500., 1.2], [2000., 1.4]]},
                'AtgmVisibilityTypeMul': {'default': 1.4, 'optic': 1.66, 'infraRed': 1.8},
                'GuidedBombVisibilityTypeMul': {'optic': 1.1, 'infraRed': 1.3},
                'AamGuidanceTypeMul': {'default': 1.1, 'arh': 1.37}},
                'exp_helicopter': {'BombRocketWeapon': {}, 'GuidedBombWeapon': {},
                    'AtgmVisibilityTypeMul': {'default': 1.8, 'infraRed': 2.1},
                    'AtgmMaxDistanceMul': {'dist': [[2000., .9], [2001., 1.], [6000., 1.], [6001., 1.09]]},
                    'atgmHasProximityFuseMul': 1.09, 'largeRocketTntMass': 7., 'largeRocketMul': 1.7,
                    'AamGuidanceTypeMul': {'default': 1., 'saclos': 1.68, 'arh': 1.68}}}}
        self.unit = {'unitClass': 'exp_fighter', 'weapons': {
            'clean': {}, 'bombs': {'totalBombRocketMass': 2000},
            'mixed': {'totalBombRocketMass': 2000, 'totalNapalmBombMass': 2000},
            'guided': {'totalGuidedBombMass': 1250, 'guidedBombVisibilityType': ['optic', 'infraRed']},
            'aam': {'aamGuidanceType': ['default', 'arh']},
            'atgm': {'atgmVisibilityType': 'default', 'atgmMaxDistance': 6001., 'atgmHasProximityFuse': True}}}

    def calculate(self, preset, **kwargs): return cost_components(self.rules, 'unit', self.unit, preset, **kwargs)

    def test_linear_mass_interpolation_clamps_without_extrapolation(self):
        points = [[200., 1.03], [2000., 1.37]]
        self.assertEqual(interpolate(points, 0), 1.03)
        self.assertAlmostEqual(interpolate(points, 1100), 1.2)
        self.assertEqual(interpolate(points, 5000), 1.37)
        with self.assertRaises(ValueError): interpolate([[1, 1], [1, 2]], 1)
        with self.assertRaises(ValueError): interpolate([[1, float('nan')]], 1)

    def test_parameter_precedence_including_rank_override_and_mission(self):
        self.rules['TankHistorical']['override_params_by_session_rank'] = {
            'low': {'minMRank': 0, 'maxMRank': 3, 'respawn_cost_mul_by_exp_class': {'exp_fighter': 1.6}}}
        self.assertEqual(score_param(self.rules, 'exp_fighter', 0), 1.6)
        self.assertEqual(score_param(self.rules, 'exp_fighter', 0, mission={'ranks': {'max': 5}}), 1.2)
        self.assertEqual(score_param(self.rules, 'exp_fighter', 0, mission={'customSpawnScore': {'exp_fighter': 2.}}), 2.)
        self.assertEqual(score_param(self.rules, 'reserve', 0), .18)

    def test_empty_class_override_disables_common_mass_rule(self):
        self.unit['unitClass'] = 'exp_helicopter'
        self.assertEqual(self.calculate('bombs')['weapon_multiplier'], 1.)
        self.assertEqual(self.calculate('guided')['weapon_multiplier'], 1.)
        self.assertEqual(weapon_param(self.rules, 'unit', 'exp_helicopter', 'BombRocketWeapon'), {})

    def test_bomb_napalm_increments_are_capped_at_largest_ceiling(self):
        self.assertEqual(self.calculate('mixed')['weapon_multiplier'], 1.37)

    def test_guided_mass_times_strongest_visibility(self):
        self.assertAlmostEqual(self.calculate('guided')['weapon_multiplier'], 1.3 * 1.3)

    def test_air_to_air_uses_maximum_guidance_cost_not_missile_sum(self):
        self.assertEqual(self.calculate('aam')['weapon_multiplier'], 1.37)
        self.unit['weapons']['rocket_arh'] = {'aamGuidanceType': 'arh'}
        value = self.calculate('custom', custom={'rocket_arh': 12})
        self.assertEqual(value['weapon_multiplier'], 1.37)

    def test_heli_atgm_range_and_proximity_factors_multiply_after_visibility(self):
        self.unit['unitClass'] = 'exp_helicopter'
        self.assertAlmostEqual(self.calculate('atgm')['weapon_multiplier'], 1.8 * 1.09 * 1.09)
        self.unit['weapons']['atgm']['atgmMaxDistance'] = 2000.
        self.unit['weapons']['atgm']['atgmHasProximityFuse'] = False
        self.assertAlmostEqual(self.calculate('atgm')['weapon_multiplier'], 1.8 * .9)

    def test_large_rocket_threshold_uses_maximum_single_tnt_mass(self):
        self.unit['unitClass'] = 'exp_helicopter'
        self.unit['weapons']['heavy_rocket'] = {'totalBombRocketMass': 100, 'maxRocketTntMass': 7.}
        self.assertEqual(self.calculate('heavy_rocket')['weapon_multiplier'], 1.7)
        self.unit['weapons']['heavy_rocket']['maxRocketTntMass'] = 6.9
        self.assertEqual(self.calculate('custom', custom={'heavy_rocket': 20})['weapon_multiplier'], 1.)

    def test_custom_presets_sum_mass_but_take_maximum_ranges_and_union_guidance(self):
        params = preset_parameters(self.unit, 'custom', custom={'bombs': 2, 'aam': 3, 'atgm': 4})
        self.assertEqual(params['mass']['totalBombRocketMass'], 4000)
        self.assertEqual(params['atgm']['maxDistance'], 6001)
        self.assertEqual(params['aam']['guidanceTypeArr'], ['default', 'arh'])

    def test_belts_add_surcharges_without_multiplying_weapon_cost(self):
        self.unit['modifications'] = {'ap': {'spawnCostMul': 1.2}, 'api': {'spawnCostMul': 1.3}}
        value = self.calculate('bombs', selected_belts=['ap', 'api'])
        self.assertAlmostEqual(value['bullet_multiplier'], 1.5)
        self.assertAlmostEqual(value['combined_multiplier'], 1.87)

    def test_unknown_belts_preserve_unknown_combined_cost_and_fighter_category(self):
        self.unit['modifications'] = {'ap': {'spawnCostMul': 1.2}}
        value = self.calculate('clean', mission={'useSpawnTypeByWeaponForSpawnScore': True})
        self.assertIsNone(value['combined_multiplier']); self.assertIsNone(value['spawn_group'])
        self.assertIn('selected_internal_belts', value['missing'])
        self.assertEqual(self.calculate('bombs', mission={'useSpawnTypeByWeaponForSpawnScore': True})['spawn_group'], 'bomber')

    def test_spawn_category_requires_mission_flag_and_uses_field_presence(self):
        self.assertIsNone(self.calculate('aam')['spawn_group'])
        flag = {'useSpawnTypeByWeaponForSpawnScore': True}
        self.assertEqual(self.calculate('aam', mission=flag)['spawn_group'], 'exp_fighter')
        self.unit['weapons']['empty_bomb_field'] = {'totalBombRocketMass': 0}
        self.assertEqual(self.calculate('empty_bomb_field', mission=flag)['spawn_group'], 'bomber')

    def test_charged_belt_changes_category_but_free_armor_piercing_belt_does_not(self):
        self.unit['modifications'] = {'paid': {'spawnCostMul': 1.2}, 'free_ap': {}}
        flag = {'useSpawnTypeByWeaponForSpawnScore': True}
        self.assertEqual(self.calculate('clean', selected_belts=['paid'], mission=flag)['spawn_group'], 'bomber')
        self.assertEqual(self.calculate('clean', selected_belts=['free_ap'], mission=flag)['spawn_group'], 'exp_fighter')

    def test_unknown_identity_quantity_and_duplicate_belts_fail(self):
        for kwargs in ({'custom': {'unknown': 1}}, {'custom': {'bombs': 0}}, {'custom': {'bombs': True}}):
            with self.assertRaises(ValueError): self.calculate('custom', **kwargs)
        for selected in (['missing'], ['ap', 'ap'], [{}]):
            with self.assertRaises(ValueError): belt_multiplier(self.unit, selected)

    def test_components_never_invent_final_base_cost_or_rounding(self):
        value = self.calculate('bombs')
        self.assertEqual(value['base_class_coefficient'], 1.2)
        self.assertEqual(value['base_respawn_points_coefficient'], 450)
        self.assertIsNone(value['final_spawn_points'])
        self.assertNotEqual(value['status'], 'official_verified')

    def test_script_reconciliation_allows_only_line_ending_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'script.nut'; path.write_bytes(b'one\ntwo\n'); expected = script_digest(path)
            path.write_bytes(b'one\r\ntwo\r\n'); self.assertEqual(script_digest(path), expected)
            path.write_bytes(b'one\r\ntwo \r\n'); self.assertNotEqual(script_digest(path), expected)

    def test_real_import_path_checks_script_and_preserves_quarantined_loadouts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'rules'; (source / 'version').parent.mkdir()
            (source / 'version').write_text('test', encoding='utf-8')
            (source / RANKS_PATH).parent.mkdir(parents=True); (source / RANKS_PATH).write_text('reviewed fixture', encoding='utf-8')
            write_json(source / WPCOST_PATH, {'unit': self.unit})
            write_json(source / 'char.vromfs.bin_u/config/warpoints.blkx', {'respawn_points': self.rules})
            receipt = {'files': {RANKS_PATH: {'url': 'https://example.test/pinned-script'}}}; write_json(source / 'capture.json', receipt)
            (source / 'gui.vromfs.bin_u/std').mkdir(); (source / 'gui.vromfs.bin_u/std/math.nut').write_text('interpolation fixture', encoding='utf-8')
            (root / 'gui.pack').write_bytes(b'gui fixture')
            capture_path = root / 'client-capture.json'; write_json(capture_path, {'packs': {'gui.vromfs.bin': {
                'version': 'test', 'saved': str(root / 'gui.pack'), 'sha256': file_hash(root / 'gui.pack'),
                'extracted': str(source / 'gui.vromfs.bin_u')}}})
            catalog = {'snapshot': {'datamine_reference': 'a'*40, 'datamine_game_version': 'test'},
                'vehicles': [{'id': 'unit', 'rule_snapshot_reference': {'binding': {'source_id': 'unit'}},
                'loadouts': [{'id': 'bombs', 'legal': True, 'source_status': 'official_verified', 'source_version': 'test'},
                            {'id': 'unverified', 'legal': False, 'spawn_cost_components': {'stale': True}},
                            {'id': 'bombs::primary=gun-upgrade', 'source_preset_id': 'bombs',
                             'primary_armament_id': 'gun-upgrade', 'primary_armament_sha256': 'b'*64,
                             'legal': True, 'source_status': 'official_verified', 'source_version': 'test'}]}]}
            path = root / 'catalog.json'; write_json(path, catalog); before = file_hash(path)
            with patch('wt_atlas.forecast.native_spawn.verify_capture', return_value=receipt), patch('wt_atlas.forecast.native_spawn.RANKS_SCRIPT_SHA256', file_hash(source / RANKS_PATH)), patch('wt_atlas.forecast.native_spawn.MATH_SCRIPT_SHA256', file_hash(source / 'gui.vromfs.bin_u/std/math.nut')):
                result, report = import_components(path, source, capture_path)
            self.assertEqual(file_hash(path), before); self.assertEqual(report['complete_multipliers'], 2)
            components=result['vehicles'][0]['loadouts'][2]['spawn_cost_components']
            self.assertEqual(components['source_preset_id'], 'bombs')
            self.assertEqual(components['primary_armament_id'], 'gun-upgrade')
            self.assertEqual(components['combined_multiplier'], result['vehicles'][0]['loadouts'][0]['spawn_cost_components']['combined_multiplier'])
            self.assertNotIn('spawn_cost_components', result['vehicles'][0]['loadouts'][1])
            self.assertNotIn('spawn_rule', result['vehicles'][0]); self.assertEqual(report['generated_final_costs'], 0)
            with patch('wt_atlas.forecast.native_spawn.verify_capture', return_value=receipt):
                with self.assertRaisesRegex(ValueError, 'Unreviewed spawn script'): import_components(path, source, capture_path)


if __name__ == '__main__': unittest.main()
