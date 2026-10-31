import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from wt_atlas.kinetic_research_batch import plan_targets
from wt_atlas.kinetic_missile_points import ADAPTER, DISTANCES


class KineticResearchBatchTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {'vehicles': [{'id': 'owner', 'kind': 'ground', 'nation': 'a', 'br': {'current': 10.},
                                     'ammo': [{'id': 'owner::missile'}]},
                                    {'id': 'enemy', 'kind': 'ground', 'nation': 'b', 'br': {'current': 10.}},
                                    {'id': 'friendly', 'kind': 'ground', 'nation': 'a', 'br': {'current': 10.}},
                                    {'id': 'component', 'kind': 'ground', 'nation': 'b', 'system_role': 'component', 'br': {'current': 10.}},
                                    {'id': 'out_of_range', 'kind': 'ground', 'nation': 'b', 'br': {'current': 12.}}]}
        yaws = list(range(0, 360, 15))
        self.config = {'matchmaking': {'spread_steps': 3}, 'scenarios': [
            {'physical_distance_m': d, 'yaw_deg': y} for d in DISTANCES for y in yaws]}
        self.bindings = {'enemy': {'target_sha256': 'geometry', 'source_job_sha256': 'job',
                                   'identity': {'vehicle_id': 'enemy', 'client_version': 'v', 'datamine_commit': 'c',
                                                'plan': {'angles_deg': yaws}}}}
        self.profiles = [{'distance_m': d, 'reference_only': True, 'provenance': {'adapter': ADAPTER,
                          'penetration_source_ammo_ids': ['owner::missile'], 'client_version': 'v', 'datamine_commit': 'c'}}
                         for d in DISTANCES]

    def test_base_scope_and_real_geometry_required(self):
        result = plan_targets(self.catalog, self.config, self.bindings, self.profiles)
        self.assertEqual([x['vehicle_id'] for x in result['targets']], ['enemy'])
        self.assertEqual(result['planned_maps'], 4); self.assertFalse(result['publishable'])
        self.bindings['enemy']['identity']['plan']['angles_deg'].pop()
        with self.assertRaisesRegex(ValueError, '24-yaw'): plan_targets(self.catalog, self.config, self.bindings, self.profiles)

    def test_incomplete_scenarios_or_promoted_profile_rejected(self):
        self.config['scenarios'].pop()
        with self.assertRaisesRegex(ValueError, '24 yaw'): plan_targets(self.catalog, self.config, self.bindings, self.profiles)
        self.config['scenarios'].append({'physical_distance_m': 1000., 'yaw_deg': 345})
        altered = copy.deepcopy(self.profiles); altered[0]['reference_only'] = False
        with self.assertRaisesRegex(ValueError, 'reference-only'): plan_targets(self.catalog, self.config, self.bindings, altered)


if __name__ == '__main__': unittest.main()
