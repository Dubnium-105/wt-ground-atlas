import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from test_forecast import fixture,settings
from wt_atlas.forecast.data import Atlas
from wt_atlas.forecast.engine import Forecast,ShotCache
from wt_atlas.forecast.rankings import order
from wt_atlas.forecast.search import Optimizer
from wt_atlas.forecast.system_forecast import system_shot


class SystemForecastTests(unittest.TestCase):
    def system(self):
        members=[{'vehicle_id':'base','role':'basic','count':1},{'vehicle_id':'part','role':'component','count':2}]
        source={'basic_vehicle_id':'base','members':members,'provenance':{'method':'official-Wiki-multiunit-membership','url':'wiki'}}
        base={'id':'base','kind':'ground','nation':'a','system_role':'basic','system_id':'base','wiki_url':'wiki','multi_vehicle_system':source}
        part={'id':'part','kind':'ground','nation':'a','system_role':'component','system_id':'base','multi_vehicle_system':{'members':members}}
        enemy={'id':'enemy','kind':'ground','nation':'b'}
        return base,part,enemy

    def engine(self):
        base,part,enemy=self.system();engine=Forecast.__new__(Forecast)
        engine.catalog={'vehicles':[base,part,enemy]};engine.atlas=type('Atlas',(),{'catalog':{v['id']:v for v in (base,part,enemy)}})()
        class Cache:
            def __init__(self):self.rows={}
            def evidence_put(self,key,level,value):self.rows[key]=(level,value)
        engine.cache=Cache();engine.skills=[(0.,1.)];engine.enemy_skills=[(0.,1.)]
        engine.config={'scenarios':[{'id':'scene','physical_distance_m':10.,'horizon_s':1.}],
                       'timing':{'simultaneous_tolerance_s':1e-9,'max_events':10},'endpoint':'penetration'}
        profile={'name':'profile','mode':'KE','distance_m':10.}
        def variant(vehicle):return {'key':vehicle['id'],'vehicle_id':vehicle['id'],'vehicle':vehicle,'ammo':{'id':'round'},'profiles':(profile,)}
        engine.by_key={v['id']:variant(v) for v in (base,enemy)}
        def single(attacker,target,scenario,skill,*,projectile_index=0):
            value={'base':.2,'part':.8,'enemy':.4}[target['vehicle_id']]
            return {'lower':value,'upper':value,'mean':value,'unknown_mass':0.,'lethality':None,
                    'aim_cache_key':'shot-'+target['vehicle_id'],'source_binding':'independent_geometry_verified'}
        engine._single_shot=single
        return engine

    def test_system_shot_has_member_bounds_and_never_invents_mean(self):
        engine=self.engine();attacker=engine.by_key['enemy'];target=engine.by_key['base'];scenario=engine.config['scenarios'][0]
        result=system_shot(engine,attacker,target,scenario,0.)
        self.assertEqual((result['lower'],result['upper'],result['mean']),(.2,.8,None))
        self.assertEqual(engine.cache.rows[result['aim_cache_key']][0],'system_shot')
        original=engine._single_shot
        engine._single_shot=lambda a,t,s,k,**kw:None if t['vehicle_id']=='part' else original(a,t,s,k,**kw)
        result=system_shot(engine,attacker,target,scenario,0.)
        self.assertEqual((result['lower'],result['upper']),(0.,1.))
        engine._single_shot=lambda *a,**kw:{**original(*a,**kw),'source_binding':'catalog_hash_verified'}
        result=system_shot(engine,attacker,target,scenario,0.)
        self.assertEqual((result['lower'],result['upper']),(0.,1.))
        target['vehicle']['forecast_physics_invalidated']=True
        self.assertIsNone(system_shot(engine,attacker,target,scenario,0.))

    def test_pair_propagates_unknown_system_bounds_without_a_score(self):
        engine=self.engine();timing={'valid':True,'first_s':0.,'cycle_s':1.,'exposure_end_s':1.,'ready_capacity':1,'missing':[]}
        with patch('wt_atlas.forecast.engine.weapon_timing',return_value=timing):
            result=engine.pair('enemy','base','scene')
        self.assertTrue(result['system_target_selection_unmodeled']);self.assertIsNone(result['score'])
        self.assertLess(result['lower'],result['upper']);self.assertEqual(result['coverage'],0.)
        self.assertIn('system_target_selection_unmodeled',result['missing'])
        self.assertEqual(result['estimate_kind'],'system_selection_lower_bound_not_expected_value')

    def test_lineup_with_unmodeled_system_never_exposes_an_estimate_or_rank(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);catalog,atlas_path,ammunition=fixture(root);config=settings();cache=ShotCache(root/'shots.sqlite')
            try:
                engine=Forecast(catalog,Atlas(atlas_path,ammunition,catalog),config,cache)
                keys=tuple(sorted(v['key'] for v in engine.variants if v['nation']=='a'))
                row={'mean':.4,'lower':.2,'upper':.8,'coverage':1.,'production_coverage':1.,
                     'assumed_coverage':0.,'usable':True,'system_target_selection_unmodeled':True}
                with patch.object(engine,'phase',return_value=row),patch.object(engine,'mission',return_value=row):
                    optimizer=Optimizer(engine);result=optimizer.evaluate(keys)
                    self.assertFalse(result['rankable']);self.assertIsNone(result['score']);self.assertIsNone(result['estimate'])
                    self.assertEqual(result['status'],'system_target_selection_unmodeled')
                    self.assertTrue(optimizer._rank(keys));self.assertTrue(order(result))
                    explained=optimizer.explain(result)
                    self.assertEqual(explained['explanation_status'],'system_target_selection_unmodeled')
                    self.assertEqual(len(explained['environment_threats']),len(result['cases']))
            finally:cache.close()

    def test_system_uncertainty_propagates_through_mission_and_mixed_phase(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);catalog,atlas_path,ammunition=fixture(root);config=settings();cache=ShotCache(root/'shots.sqlite')
            try:
                engine=Forecast(catalog,Atlas(atlas_path,ammunition,catalog),config,cache)
                own=next(v for v in engine.variants if v['nation']=='a');enemy=next(v for v in engine.variants if v['nation']=='b')
                pair={'pair_key':'pair-fixture','mean':.2,'lower':.2,'upper':.8,'coverage':0.,'system_target_selection_unmodeled':True}
                with patch.object(engine,'opponents',return_value=[(enemy['key'],1.)]),patch.object(engine,'pair',return_value=pair):
                    row=engine.mission(own['key'],'ground',own['br_step'],config['scenarios'][0]['id'])
                    self.assertTrue(row['system_target_selection_unmodeled'])
                    self.assertEqual(row['estimate_kind'],'conservative_lower_bound_not_expected_value')
                    engine.phase_tasks=[{'ground':.5,'anti_tank':.5}]
                    mixed=engine.phase(own['key'],0,own['br_step'],config['scenarios'][0]['id'])
                    self.assertTrue(mixed['system_target_selection_unmodeled'])
                    self.assertEqual(mixed['estimate_kind'],'conservative_lower_bound_not_expected_value')
            finally:cache.close()


if __name__=='__main__':unittest.main()
