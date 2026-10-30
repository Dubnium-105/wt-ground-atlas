import copy
import itertools
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from test_forecast import fixture,settings
from wt_atlas.forecast.data import Atlas,canonical,digest
from wt_atlas.forecast.engine import Forecast,ShotCache,enumerate_variants
from wt_atlas.forecast.search import Optimizer,feasible,sortie_policy
from wt_atlas.forecast.capabilities import loadout_errors,spawn_cost,validate_mission
from wt_atlas.forecast.versions import apply_version,invalidation,release_gate,selection_identity
from wt_atlas.forecast.aim import integrate


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.cat,self.aroot,self.ammo=fixture(self.root);self.c=settings()
        self.atlas=Atlas(self.aroot,self.ammo,self.cat);self.cache=ShotCache(self.root/'cache.sqlite')

    def tearDown(self):self.cache.close();self.tmp.cleanup()

    def engine(self):return Forecast(self.cat,self.atlas,self.c,self.cache)

    def test_automatic_multi_br_configuration_world_matches_oracle(self):
        for v in self.cat['vehicles']:
            v['role']='light' if v['id'].endswith('1') else 'mbt'
            v['rating_configurations']={'ground_rb':{'status':'official_verified','source':{'url':'synthetic'},'options':[
                {'id':'low','br':{'current':9.3},'allowed_ammo_ids':[v['ammo'][0]['id']]},
                {'id':'high','br':{'current':9.7},'allowed_ammo_ids':[v['ammo'][0]['id']]}]}}
        self.c['search'].update(slots=[1,2],pareto=True,seed=17)
        e=self.engine();o=Optimizer(e);world=o.world()
        self.assertEqual({t['nation'] for t in world['tables']},{'a','b'})
        self.assertEqual({t['cap_step'] for t in world['tables']},{28,29})
        for table in world['tables']:
            pool=[v for v in e.variants if v['nation']==table['nation'] and v['br_step']<=table['cap_step']]
            brute=[o.evaluate(tuple(sorted(v['key'] for v in lineup))) for n in range(1,table['slots']+1) for lineup in itertools.combinations(pool,n) if feasible(lineup,table['slots']) and max(v['br_step'] for v in lineup)==table['cap_step']]
            self.assertAlmostEqual(table['candidates'][0]['estimate'],max(r['estimate'] for r in brute))
            self.assertFalse(table['budget_exhausted'])
        self.assertEqual(world,Optimizer(e).world())

    def test_sampler_legal_normalized_and_repeatable(self):
        self.c['matchmaking']['lineup_sampler']={'enabled':True,'samples':30,'slots':2,'seed':8,'status':'assumed'}
        e=self.engine();rows=e.opponent_lineups('a',28)
        self.assertAlmostEqual(sum(w for _,w in rows),1)
        for keys,w in rows:self.assertTrue(feasible(tuple(e.by_key[k] for k in keys),2))
        self.assertEqual(rows,self.engine().opponent_lineups('a',28))
        self.assertAlmostEqual(sum(w for _,w in e.opponents('a',28,'ground')),1)

    def test_released_patch_becomes_baseline_and_br_reuses_physics(self):
        patch={'status':'released','effective_date':'2026-10-01','entries':[{'vehicle_id':self.cat['vehicles'][0]['id'],'field':'br','old':9.3,'new':9.7}]}
        current,planned,audit=apply_version(self.cat,patch)
        self.assertIsNone(planned);self.assertEqual(current['vehicles'][0]['br']['current'],9.7)
        row=invalidation(self.cat,current)[0]
        self.assertTrue(row['reuse_physical_maps']);self.assertNotIn('physics',row['layers'])

    def test_version_key_isolated_but_selection_migrates(self):
        before=self.engine().variants
        self.cat['snapshot']['client_source']['version']='new-version'
        after=self.engine().variants
        self.assertNotEqual(before[0]['key'],after[0]['key'])
        self.assertEqual({selection_identity(v) for v in before},{selection_identity(v) for v in after})

    def test_payload_restrictions_and_unlock_do_not_raise_br(self):
        v={'id':'plane'};l={'id':'p','legal':True,'weapons':[{'id':'advanced','station':1,'count':1,'mass_kg':100}]}
        self.assertIn('forbidden_by_rating_configuration',loadout_errors(l,v,{'allowed_weapon_ids':['basic']}))
        self.assertFalse(loadout_errors(l,v,{'allowed_weapon_ids':['advanced'],'required_modification_ids':['research']},unlocked=['research'],require_unlocked=True))
        self.assertIn('modifications_unavailable',loadout_errors(l,v,{'required_modification_ids':['research']},unlocked=[],require_unlocked=True))
        l['weapons'].append(dict(l['weapons'][0]))
        self.assertIn('mutually_exclusive_station',loadout_errors(l,v,{}))

    def test_spawn_escalation_changes_backup_feasibility(self):
        c=self.c['spawn'];c.update(initial_points=300,point_gains=[{'points':0,'probability':1.}])
        item={'mean':1.,'lower':1.,'upper':1.,'coverage':1.}
        rows=[[item,item]]*2
        flat=sortie_policy(rows,[150,150],c)
        increasing=sortie_policy(rows,[150,150],c,domains=['ground','ground'],cost_function=lambda i,n:150*2**n)
        self.assertEqual(flat['mean'],1.)
        self.assertEqual(increasing['mean'],.6)
        self.assertIsNone(increasing['policy'][1]['actions'][0]['member_index'])

    def test_mission_max_range_not_hit_rate_and_bound_payload(self):
        e=self.engine();v=e.variants[0]
        r={'variant_key':v['key'],'target_vehicle_id':'target','scenario_id':self.c['scenarios'][0]['id'],'task':'cas','lower':.1,'mean':.2,'upper':.3,'source_version':'synthetic-only','source':'synthetic','status':'validated',
           'evidence':{'loadout_id':None,'flight_conditions':{},'radar_conditions':{},'target_countermeasures':{},'source_sha256':'0'*64,'measurement':'maximum_range'}}
        with self.assertRaises(ValueError):validate_mission(r,'synthetic-only',e.by_key,{r['scenario_id']})
        r['evidence']['measurement']='controlled_task_observation'
        self.assertTrue(validate_mission(r,'synthetic-only',e.by_key,{r['scenario_id']})['production_validated'])

    def test_gate_cannot_promote_synthetic_geometry_or_truncated_search(self):
        e=self.engine();world=Optimizer(e).world();world['truncated_searches']=1
        gate=release_gate(self.cat,self.atlas,self.c,world)
        self.assertFalse(gate['publishable'])
        self.assertIn('synthetic_fixture_not_production',gate['why_not_publishable'])
        self.assertIn('search_budget_truncated',gate['why_not_publishable'])
        self.assertIn('independent_target_geometry_not_verified',gate['why_not_publishable'])

    def test_pixel_resolution_and_unknown_monotonicity(self):
        values=[]
        for size in (40,80):
            p=np.full((size,size),.8,np.float32);s=np.full(p.shape,3,np.uint8)
            r=integrate(p,s,2/size,0,self.c['scenarios'][0],self.c['aim']);values.append(r['mean'])
            s[0:size//2]=2;unknown=integrate(p,s,2/size,0,self.c['scenarios'][0],self.c['aim'])
            self.assertLessEqual(unknown['lower'],unknown['mean']);self.assertGreaterEqual(unknown['upper'],unknown['mean'])
        self.assertAlmostEqual(*values,places=5)

    def test_truncated_results_do_not_enter_board(self):
        self.c['search']['max_evaluations']=1
        world=Optimizer(self.engine()).world()
        self.assertGreater(world['truncated_searches'],0)
        self.assertFalse(any(world['boards'].values()))


if __name__=='__main__':unittest.main()
