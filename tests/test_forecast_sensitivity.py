"""Sensitivity is measured on real optimizer worlds; a Boolean cannot pass it."""
import copy
import tempfile
import unittest
from pathlib import Path
from test_forecast import fixture,settings
from wt_atlas.forecast.data import Atlas,digest,validate_config,validate_sensitivity_policy
from wt_atlas.forecast.engine import Forecast,ShotCache
from wt_atlas.forecast.search import Optimizer
from wt_atlas.forecast.sensitivity import run,valid,compare,CASES

class SensitivityTests(unittest.TestCase):
    def test_versioned_policy_controls_actual_perturbations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,atlas_root,ammo=fixture(root);config=settings()
            policy=config['sensitivity']
            policy.update(aim_narrower_factor=.9,aim_wider_factor=1.1,
                          opponent_skill_mean_shift=.25,search_budget_multiplier=3,seed_offset=7)
            policy['thresholds']={'minimum_overlap':.6,'maximum_score_delta':20.}
            cache=ShotCache(root/'shots.sqlite')
            try:
                engine=Forecast(catalog,Atlas(atlas_root,ammo,catalog),config,cache)
                result=run(engine,Optimizer(engine).world(),inputs_sha256='policy-fixture')
                self.assertEqual(result['policy'],policy)
                expected=copy.deepcopy(config);expected['search']['max_evaluations']*=3
                measured=next(r for r in result['cases'] if r['case']=='increased_search_budget')
                self.assertEqual(measured['config_sha256'],digest(expected))
                expected=copy.deepcopy(config)
                expected['search']['seed']+=7;expected['matchmaking']['lineup_sampler']['seed']+=7
                self.assertEqual(next(r for r in result['cases'] if r['case']=='alternate_queue_seed')['config_sha256'],digest(expected))
                self.assertTrue(all(r['evaluations']>0 for r in result['cases']))
                baseline={'boards':{'test':[{'keys':['a'],'score':50.}]},'truncated_searches':0}
                changed={'boards':{'test':[{'keys':['a'],'score':65.}]},'truncated_searches':0}
                self.assertTrue(compare(baseline,changed,**policy['thresholds'])['passed'])
                self.assertFalse(compare(baseline,changed,**settings()['sensitivity']['thresholds'])['passed'])
            finally:cache.close()

    def test_invalid_or_missing_policy_cannot_be_used_for_validation(self):
        for field,value in [('aim_narrower_factor',1.),('aim_wider_factor',True),
                            ('opponent_skill_mean_shift',0.),('search_budget_multiplier',1),
                            ('seed_offset',False),('schema_version','unknown')]:
            config=settings();config['sensitivity'][field]=value
            with self.assertRaises(ValueError):validate_config(config)
        for field,value in [('minimum_overlap',0.),('maximum_score_delta',float('nan'))]:
            config=settings();config['sensitivity']['thresholds'][field]=value
            with self.assertRaises(ValueError):validate_config(config)
        legacy=settings();del legacy['sensitivity']
        validate_config(legacy)  # Historical diagnostic configs remain readable.
        with self.assertRaisesRegex(ValueError,'explicit versioned'):
            validate_sensitivity_policy(legacy.get('sensitivity'))
        self.assertFalse(valid({'passed':True},{},legacy))

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
        self.assertFalse(compare(empty,empty,**settings()["sensitivity"]["thresholds"])['passed'])
        world={'boards':{'test':[{'keys':['a'],'score':50.}]},'truncated_searches':0}
        truncated={**world,'truncated_searches':1}
        self.assertFalse(compare(world,truncated,**settings()["sensitivity"]["thresholds"])['passed'])
        changed={'boards':{'test':[{'keys':['a'],'score':90.}]},'truncated_searches':0}
        self.assertFalse(compare(world,changed,**settings()["sensitivity"]["thresholds"])['passed'])
        self.assertFalse(valid({'passed':True}, {}, {}))

if __name__=='__main__':unittest.main()
