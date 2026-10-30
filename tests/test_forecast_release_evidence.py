"""Publication must be supported by each ranked lineup, not unrelated records."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from test_forecast import fixture, settings
from wt_atlas.forecast.data import Atlas, digest
from wt_atlas.forecast.engine import Forecast, ShotCache
from wt_atlas.forecast.search import Optimizer, sortie_policy
from wt_atlas.forecast.versions import release_gate

class ReleaseEvidenceTests(unittest.TestCase):
    def test_waiting_does_not_invent_task_evidence(self):
        row={'mean':0.,'lower':0.,'upper':1.,'coverage':0.,'usable':False}
        result=sortie_policy([[row],[row]],[None],settings()['spawn'])
        self.assertEqual(result['mean'],0.)
        self.assertEqual(result['coverage'],1.)  # Waiting has a known zero reward.
        self.assertEqual(result['evidence_coverage'],0.)  # It supplies no task evidence.

    def test_resumed_table_counts_are_not_reported_as_new_evaluations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,atlas_root,ammo=fixture(root)
            cache=ShotCache(root/'cache.sqlite')
            try:
                engine=Forecast(catalog,Atlas(atlas_root,ammo,catalog),settings(),cache)
                engine.build_key='execution-receipt-fixture'
                first=Optimizer(engine).world()
                engine.resume=True
                resumed=Optimizer(engine).world()
                self.assertGreater(first['execution']['new_evaluations'],0)
                self.assertEqual(resumed['execution']['new_evaluations'],0)
                self.assertEqual(resumed['execution']['restored_tables'],len(first['tables']))
                self.assertEqual(first['boards'],resumed['boards'])
            finally:cache.close()

    def gate_inputs(self):
        # Protocol fixtures only; these are never published game data.
        catalog={'snapshot':{'snapshot_id':'fixture','game_version':'fixture',
            'datamine_commit':'a'*40,'wiki_collected_at':'2026-10-01T00:00:00Z',
            'effective_date':'2026-10-01','source_status':'released'},'vehicles':[
                {'id':'fixture','br_provenance':{'source':'fixture','source_version':'fixture'},
                 'spawn_rule':{'source':'fixture','source_version':'fixture','status':'official_verified'},'loadouts':[]}]}
        atlas=SimpleNamespace(meta={'map':{'source_version':'fixture','datamine_commit':'a'*40,
                                          'target_binding':'independent_geometry_verified'}})
        config=settings();config['publication'].update(scope='full_ground_rb',required_tasks=['cas'])
        config['spawn'].update(status='official_verified',phases=['cas','cas'])
        world={'truncated_searches':0,'boards':{'2/all':[{'lineup_key':'fixture',
            'production_mission_coverage':{'cas':1.}}]}}
        kwargs={'inventory':{'verified_arrays':1,'selected_maps':1},
                'missions':{'fixture':{'task':'cas','production_validated':True}},
                'sensitivity':{'passed':True,'catalog_sha256':digest(catalog),'config_sha256':digest(config)}}
        return catalog,atlas,config,world,kwargs

    def test_complete_context_can_pass_but_unrelated_evidence_cannot(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        self.assertTrue(release_gate(cat,atlas,config,world,**kwargs)['publishable'])
        world['boards']['2/all'][0]['production_mission_coverage']['cas']=0.
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertFalse(result['publishable'])
        self.assertIn('ranked_lineup_mission_evidence_incomplete',result['why_not_publishable'])

    def test_required_tasks_must_be_part_of_the_actual_model(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        config['spawn']['phases']=['ground','ground']
        kwargs['sensitivity']['config_sha256']=digest(config)
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertFalse(result['publishable'])
        self.assertIn('required_tasks_not_modeled',result['why_not_publishable'])

    def test_declared_validation_without_delivery_evidence_stays_research_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,atlas_root,ammo=fixture(root)
            config=settings();config['spawn']['phases']=['cas','cas']
            cache=ShotCache(root/'cache.sqlite')
            try:
                engine=Forecast(catalog,Atlas(atlas_root,ammo,catalog),config,cache)
                for own in engine.variants:
                    for enemy in engine.variants:
                        if own['nation']==enemy['nation']:continue
                        engine.missions[(own['key'],enemy['vehicle_id'],config['scenarios'][0]['id'],'cas')]={
                            'lower':.6,'mean':.6,'upper':.6,'status':'validated',
                            'production_validated':False,'evidence_sha256':digest([own['key'],enemy['key']])}
                keys=tuple(sorted(v['key'] for v in engine.variants if v['nation']=='a'))
                result=Optimizer(engine).evaluate(keys)
                self.assertGreater(result['evidence_coverage'],.8)
                self.assertEqual(result['production_mission_coverage']['cas'],0.)
            finally:cache.close()

if __name__=='__main__':unittest.main()

