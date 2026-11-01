import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from wt_atlas.kinetic_missile_points import profiles
from wt_atlas.forecast.data import Atlas


class KineticMissilePointTests(unittest.TestCase):
    def setUp(self):
        rocket = {'bulletType': 'atgm_ke_tank', 'penetrationBySpeed': True, 'caliber': .163,
                  'damageCaliber': [.03, .03], 'minDistance': 400, 'maxDistance': 5000,
                  'damage': {'kinetic': {'damageType': 'generic'}}, 'slopeEffectPreset': 's', 'ricochetPreset': 'r'}
        bullet = {'bulletType': 'atgm_ke_tank', 'bulletName': 'projectile', 'damageCaliber': .03, 'rocket': rocket}
        self.binding = {'bullets': [bullet], 'is_belt': False, 'weapon_path': 'weapon', 'weapon_sha256': 'hash',
                        'localization': [{'key': 'projectile', 'value': 'MGM'}]}
        self.points = [{'distance_m': d, 'angle_deg': 0., 'mm': mm}
                       for d, mm in [(10., 20.), (100., 124.), (500., 416.), (1000., 764.)]]
        self.ammo = {'id': 'v::MGM', 'name': 'MGM', 'caliber_mm': 163., 'penetration': self.points}
        self.table = {'points': self.points, 'maximum_only': False}
        self.response = {'dispersion': .1, 'slope': {'s': {'row': {'caliberToArmor': 1., 'slopeEffect': [0., 1.]}}},
                         'ricochet': {'r': {'row': {'caliberToArmor': 1., 'ricochetProbability': [0., .2]}}}, 'hashes': {}}

    def run_profiles(self):
        return profiles(self.ammo, self.binding, self.table, {'sha256': 'wiki'}, self.response, 'v', 'commit')

    def test_acceleration_uses_exact_points_and_blocks_ranking(self):
        values = self.run_profiles()
        self.assertEqual([x['penetration_mm'] for x in values], [20., 124., 416., 764.])
        self.assertEqual([x['provenance']['inside_declared_distance_bounds'] for x in values], [False, False, True, True])
        self.assertTrue(all(x['reference_only'] and x['normalized_distance_curve'] == [] for x in values))

    def test_missing_points_or_changed_source_are_rejected(self):
        self.table = copy.deepcopy(self.table); self.table['points'].pop()
        with self.assertRaises(ValueError): self.run_profiles()
        self.table = {'points': self.points, 'maximum_only': False}
        self.binding['bullets'][0]['rocket']['penetrationBySpeed'] = False
        with self.assertRaises(ValueError): self.run_profiles()

    def test_ambiguous_caliber_or_localization_are_rejected(self):
        self.binding['bullets'][0]['rocket']['damageCaliber'] = [.03, .04]
        with self.assertRaises(ValueError): self.run_profiles()
        self.binding['bullets'][0]['rocket']['damageCaliber'] = [.03, .03]
        self.binding['localization'][0]['value'] = 'different'
        with self.assertRaises(ValueError): self.run_profiles()

    def test_research_profiles_cannot_be_bound_for_ranking(self):
        vehicle = {'id': 'v', 'nation': 'test', 'ammo': [self.ammo],
                   'source_attributes': {'client': {'source_version': 'v'}}}
        class EmptyIndex:
            metadata = {}; lookup = {}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'ammo.json'
            source.write_text(json.dumps(self.run_profiles()), encoding='utf8')
            atlas = Atlas(root, source, {'vehicles': [vehicle]}, index=EmptyIndex())
            self.assertEqual(atlas.bindings(vehicle), [])


if __name__ == '__main__':
    unittest.main()
