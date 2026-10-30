import copy
import json
from pathlib import Path
import tempfile
import sys
import unittest

from test_forecast import fixture, settings, ROOT
from wt_atlas.forecast.data import Atlas
from wt_atlas.forecast.engine import Forecast, ShotCache
from wt_atlas.forecast.search import Optimizer
from wt_atlas.forecast.rankings import build_views, select
from wt_atlas.forecast.sensitivity import compare
from wt_atlas.forecast.build import package_site
from wt_atlas.forecast.versions import release_gate
import test_forecast_release_evidence as evidence_tests
sys.path.insert(0, str(ROOT/"scripts"))
from build_product import compose


class RankingViewTests(unittest.TestCase):
    def test_every_br_and_nation_survives_global_limit_and_shared_parents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cat, atlas, ammo = fixture(root)
            for v in cat['vehicles']:
                v['rating_configurations'] = {'ground_rb': {'status': 'official_verified', 'source': {'url': 'synthetic'}, 'options': [
                    {'id': name, 'br': {'current': br}, 'allowed_ammo_ids': [v['ammo'][0]['id']]}
                    for name, br in [('low', 9.3), ('high', 9.7)]]}}
            config = settings(); config['search']['global_limit'] = 1
            cache = ShotCache(root/'cache.sqlite')
            try:
                world = Optimizer(Forecast(cat, Atlas(atlas, ammo, cat), config, cache)).world()
                for board in world['boards']:
                    self.assertEqual(len(world['boards'][board]), 1)
                    self.assertEqual({r['actual_br'] for r in world['views']['br'][board]}, {9.3, 9.7})
                    self.assertEqual({r['nation'] for r in world['views']['nation'][board]}, {'a', 'b'})
                package_site(root/'published', {'build_key': 'fixture', 'sample_subset': False}, {'current': world}, [], ROOT)
                index = json.loads((root/'published/index.json').read_text())
                overview = json.loads((root/'published'/index['worlds']['current']['url']).read_text())
                shard = overview['views']['br']['2/all']
                self.assertEqual(len(json.loads((root/'published'/shard['url']).read_text())), 2)
                self.assertTrue((root/'published/views.mjs').is_file())
                compose(root/'published', root/'unified')
                old_index = (root/'unified/index.json').read_bytes()
                (root/'published'/shard['url']).write_text('[]')
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    compose(root/'published', root/'unified')
                self.assertEqual((root/'unified/index.json').read_bytes(), old_index)

                # A sample's category shards remain empty even with valid synthetic arrays.
                package_site(root/'sample', {'build_key': 'fixture', 'sample_subset': True}, {'current': world}, [], ROOT)
                si = json.loads((root/'sample/index.json').read_text())
                sw = json.loads((root/'sample'/si['worlds']['current']['url']).read_text())
                self.assertEqual(json.loads((root/'sample'/sw['views']['br']['2/all']['url']).read_text()), [])
            finally:
                cache.close()

    def test_category_alternative_is_searched_even_outside_ordinary_shortlist(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); cat, atlas, ammo = fixture(root)
            cat['vehicles'].append({'id': 'synthetic_plane', 'name': 'synthetic plane', 'nation': 'a',
                'kind': 'aircraft', 'role': 'fighter', 'br': {'current': 9.3},
                'loadouts': [{'id': 'clean', 'legal': True, 'weapons': []}]})
            config = settings(); config['search'].update(per_cap_limit=1, minimum_pair_coverage=.5)
            cache = ShotCache(root/'cache.sqlite')
            try:
                world = Optimizer(Forecast(cat, Atlas(atlas, ammo, cat), config, cache)).world()
                table = next(t for t in world['tables'] if t['nation'] == 'a')
                self.assertEqual(table['candidates'][0]['composition'], 'ground_only')
                alternative = table['category_candidates']['combined_arms'][0]
                self.assertIn('synthetic_plane', {m['vehicle_id'] for m in alternative['members']})
                self.assertNotIn(alternative['lineup_key'], {r['lineup_key'] for r in table['candidates']})
                self.assertTrue(world['views']['combined_arms']['2/all'])
                for rows in world['views']['combined_arms'].values():
                    self.assertTrue(all(r['rankable'] for r in rows))
            finally:
                cache.close()

    def test_truncated_or_unknown_results_do_not_enter_any_view(self):
        row = {'lineup_key': 'r', 'keys': ['r'], 'members': [{'vehicle_id': 'r'}], 'nation': 'a',
               'actual_br': 9.3, 'score': 10., 'rankable': True, 'composition': 'ground_only', 'uptier_lower_utility': 9.}
        table = {'slots': 2, 'acquisition': 'all', 'budget_exhausted': True, 'candidates': [row],
                 'category_candidates': {'uptier': [row]}}
        self.assertFalse(any(rows for boards in build_views([table], settings()).values() for rows in boards.values()))
        self.assertEqual(select([{**row, 'score': None}], 'br'), [])

    def test_sensitivity_checks_a_category_not_in_the_global_board(self):
        global_row = {'keys': ['global'], 'score': 80.}
        before = {'boards': {'2/all': [global_row]}, 'truncated_searches': 0,
                  'views': {'global': {'2/all': [global_row]}, 'uptier': {'2/all': [{'keys': ['reserve'], 'score': 40.}]}}}
        after = copy.deepcopy(before); after['views']['uptier']['2/all'][0]['score'] = 75.
        result = compare(before, after, **settings()["sensitivity"]["thresholds"])
        self.assertFalse(result['passed'])
        self.assertEqual(next(r for r in result['boards'] if r['board'] == 'uptier/2/all')['maximum_score_delta'], 35.)

    def test_filtered_table_cannot_bypass_lineup_task_evidence_gate(self):
        cat, atlas, config, world, kwargs = evidence_tests.ReleaseEvidenceTests().gate_inputs()
        world['tables'] = [{'budget_exhausted': False, 'candidates': [
            {'lineup_key': 'outside-global', 'rankable': True, 'production_mission_coverage': {'cas': 0.}}]}]
        result = release_gate(cat, atlas, config, world, **kwargs)
        self.assertIn('ranked_lineup_mission_evidence_incomplete', result['why_not_publishable'])


if __name__ == '__main__':
    unittest.main()
