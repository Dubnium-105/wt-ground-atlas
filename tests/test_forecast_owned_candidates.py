import copy
from pathlib import Path
import tempfile
import unittest
from test_forecast import fixture,settings
from wt_atlas.forecast.data import Atlas
from wt_atlas.forecast.engine import Forecast,ShotCache
from wt_atlas.forecast.search import Optimizer


class OwnedCandidateTests(unittest.TestCase):
    def test_unknown_air_configuration_stays_in_population_but_cannot_fill_roster(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cat,atlas_root,ammo=fixture(root);config=settings()
            air=copy.deepcopy(cat['vehicles'][0]);air.update(id='unknown-plane',kind='aircraft',role='fighter',ammo=[],loadouts=[])
            cat['vehicles'].append(air);config['search']['slots']=[3]
            cache=ShotCache(root/'shots.sqlite')
            try:
                engine=Forecast(cat,Atlas(atlas_root,ammo,cat),config,cache)
                unknown=next(v for v in engine.variants if v['vehicle_id']==air['id'])
                self.assertFalse(unknown['selection_ready'])
                self.assertTrue(any(engine.by_key[key]['vehicle_id']==air['id'] for key,_ in engine.opponents('b',28,'air')))
                table=Optimizer(engine).discover('a',28,3,'all')
                self.assertEqual(table['candidate_status'],'insufficient_distinct_members')
                self.assertEqual(table['available_parent_groups'],2);self.assertEqual(table['candidates'],[])
                self.assertTrue(any(r.get('reason')=='no_verified_legal_carried_loadout' for r in engine.rejected))
            finally:cache.close()

    def test_unbound_ground_ammunition_cannot_be_selected_or_evaluated(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cat,atlas_root,ammo=fixture(root);config=settings()
            cat['vehicles'][0]['ammo']=[];cache=ShotCache(root/'shots.sqlite')
            try:
                engine=Forecast(cat,Atlas(atlas_root,ammo,cat),config,cache)
                unknown=next(v for v in engine.variants if v['vehicle_id']==cat['vehicles'][0]['id'])
                self.assertFalse(unknown['selection_ready'])
                with self.assertRaisesRegex(ValueError,'owned configuration'):Optimizer(engine).evaluate((unknown['key'],))
                self.assertFalse(Optimizer(engine).discover(unknown['nation'],28,2,'all')['candidates'])
            finally:cache.close()
