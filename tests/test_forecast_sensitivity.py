"""Sensitivity is measured on real optimizer worlds; a Boolean cannot pass it."""
import copy
import tempfile
import unittest
from pathlib import Path
from test_forecast import fixture,settings
from wt_atlas.forecast.data import Atlas,digest
from wt_atlas.forecast.engine import Forecast,ShotCache
from wt_atlas.forecast.search import Optimizer
from wt_atlas.forecast.sensitivity import run,valid,compare,CASES

class SensitivityTests(unittest.TestCase):
    def test_measures_all_perturbations_and_binds_to_exact_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,atlas_root,ammo=fixture(root);config=settings()
            cache=ShotCache(root/'shots.sqlite')
            try:
                e=Forecast(catalog,Atlas(atlas_root,ammo,catalog),config,cache)
                result=run(e,Optimizer(e).world(),inputs_sha256='fixture-inputs')
                self.assertEqual({r['case'] for r in result['cases']},set(CASES))
                self.assertTrue(all(r['evaluations']>0 for r in result['cases']))
                self.assertFalse(result['passed'])  # This short duel is sensitive to enemy first fire.
                self.assertFalse(valid(result,catalog,config,'fixture-inputs'))
                self.assertEqual([r['case'] for r in result['cases'] if not r['passed']],['opponent_skill_higher'])
                self.assertEqual(result['inputs_sha256'],'fixture-inputs')
                self.assertEqual(result['sha256'],digest({k:v for k,v in result.items() if k!='sha256'}))
            finally:cache.close()

    def test_empty_and_truncated_worlds_or_bare_claims_never_pass(self):
        empty={'boards':{},'truncated_searches':0}
        self.assertFalse(compare(empty,empty)['passed'])
        world={'boards':{'test':[{'keys':['a'],'score':50.}]},'truncated_searches':0}
        truncated={**world,'truncated_searches':1}
        self.assertFalse(compare(world,truncated)['passed'])
        changed={'boards':{'test':[{'keys':['a'],'score':90.}]},'truncated_searches':0}
        self.assertFalse(compare(world,changed)['passed'])
        self.assertFalse(valid({'passed':True}, {}, {}))

if __name__=='__main__':unittest.main()
