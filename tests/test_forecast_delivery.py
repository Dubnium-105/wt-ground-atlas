import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_forecast import fixture, settings
from wt_atlas.forecast.data import Atlas
from wt_atlas.forecast.delivery import audit_profiles, delivery_issue
from wt_atlas.forecast.engine import Forecast, ShotCache
from wt_atlas.forecast import engine as engine_module
from wt_atlas.forecast.versions import source_audit


class MissileDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.catalog, atlas_root, ammo = fixture(root); self.config = settings()
        self.atlas = Atlas(atlas_root, ammo, self.catalog); self.cache = ShotCache(root / 'cache.sqlite')
        self.engine = Forecast(self.catalog, self.atlas, self.config, self.cache)
        self.a = next(v for v in self.engine.variants if v['nation'] == 'a')
        self.b = next(v for v in self.engine.variants if v['nation'] == 'b')
        self.scenario = self.config['scenarios'][0]

    def tearDown(self): self.cache.close(); self.temp.cleanup()

    def evidence(self, minimum, maximum):
        self.a['profiles'][0]['provenance']['chemical_energy'] = {
            'source_min_distance_m': minimum, 'source_max_distance_m': maximum,
            'flight_and_activation_modeled': False}

    def test_outside_bounds_is_unknown_not_zero_even_with_a_valid_map_and_warm_cache(self):
        self.assertIsNotNone(self.engine.shot(self.a, self.b, self.scenario, 0))
        self.evidence(30., 4000.)
        self.assertIsNone(self.engine.shot(self.a, self.b, self.scenario, 0))
        pair = self.engine.pair(self.a['key'], self.b['key'], self.scenario['id'])
        self.assertIsNone(pair['score']); self.assertEqual(pair['coverage'], 0.)
        self.assertEqual((pair['lower'], pair['upper']), (0., 1.))
        self.assertIn('delivery_unverified_outside_source_distance_bounds', pair['missing'])

    def test_source_audit_blocks_an_unranked_alternative_without_excluding_it(self):
        self.evidence(30., 4000.)
        audit = source_audit(self.catalog, self.config, 'synthetic-only', self.engine.variants, [])
        self.assertIn('delivery_unverified_outside_source_distance_bounds', audit['blockers'])
        self.assertTrue(any(row['vehicle_id'] == self.a['vehicle_id'] and row['distance_m'] == 10.
                            for row in audit['delivery_unverified_profiles']))
        self.assertNotIn(self.a['vehicle_id'], audit['excluded_component_ids'])

    def test_bounds_are_inclusive_and_bad_values_remain_unknown(self):
        self.evidence(10., 10.)
        self.assertIsNone(delivery_issue(self.a['profiles'][0], 10.))
        self.assertIsNotNone(self.engine.shot(self.a, self.b, self.scenario, 0))
        for invalid in (-1, '30', True, float('nan')):
            profile = copy.deepcopy(self.a['profiles'][0])
            profile['provenance']['chemical_energy']['source_min_distance_m'] = invalid
            self.assertEqual(delivery_issue(profile, 10.), 'invalid_missile_delivery_source_bounds')
        profile = copy.deepcopy(self.a['profiles'][0])
        profile['provenance']['chemical_energy']['source_min_distance_m'] = 11.
        self.assertEqual(delivery_issue(profile, 10.), 'invalid_missile_delivery_source_bounds')

    def test_profile_audit_counts_unique_owners_and_ignores_reference_profiles(self):
        self.evidence(30., 4000.)
        profile = copy.deepcopy(self.a['profiles'][0]); profile['provenance']['penetration_source_ammo_ids'] = ['a::one', 'b::two']
        reference = copy.deepcopy(profile); reference['name'] = 'reference'; reference['reference_only'] = True
        result = audit_profiles([profile, reference], self.config)
        self.assertEqual((result['affected_profiles'], result['distinct_ammo_ids'], result['distinct_vehicles']), (1, 2, 2))
        self.assertEqual(result['profiles_by_distance_m'], {'10.0': 1})
        reference['name'] = profile['name']
        with self.assertRaisesRegex(ValueError, 'Unique'): audit_profiles([profile, reference], self.config)

    def test_delivery_code_change_invalidates_shot_cache_identity(self):
        old_hash = self.engine.aim_code_hash; original = engine_module.file_hash
        with patch.object(engine_module, 'file_hash', side_effect=lambda p:
                          'f' * 64 if Path(p).name == 'delivery.py' else original(p)):
            changed = Forecast(self.catalog, self.atlas, self.config, self.cache)
        self.assertNotEqual(old_hash, changed.aim_code_hash)


if __name__ == '__main__': unittest.main()
