"""Publication must be supported by each ranked lineup, not unrelated records."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from test_forecast import fixture, settings
from wt_atlas.forecast.data import Atlas, digest
from wt_atlas.forecast.engine import Forecast, ShotCache, enumerate_variants
from wt_atlas.forecast.search import Optimizer, sortie_policy
from wt_atlas.forecast.versions import release_gate
from wt_atlas.forecast.sensitivity import CASES

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
                {'id':'fixture','kind':'ground','nation':'fixture','br':{'current':1.},'ammo':[{'id':'fixture-round'}],
                 'br_provenance':{'source':'fixture','source_version':'fixture'},
                 'spawn_rule':{'source':'fixture','source_version':'fixture','status':'official_verified'},'loadouts':[]}]}
        atlas=SimpleNamespace(meta={'map':{'source_version':'fixture','datamine_commit':'a'*40,
                                          'target_binding':'independent_geometry_verified'}})
        atlas.bindings=lambda vehicle:[(vehicle['ammo'][0],{'name':'fixture-profile','distance_m':10.})] if vehicle.get('ammo') else []
        config=settings();config['publication'].update(scope='full_ground_rb',required_tasks=['cas'])
        config['spawn'].update(status='official_verified',phases=['cas','cas'])
        world={'truncated_searches':0,'boards':{'2/all':[{'lineup_key':'fixture',
            'production_mission_coverage':{'cas':1.}}]}}
        variant=enumerate_variants(catalog,atlas,config)[0][0]
        world['boards']['2/all'][0]['members']=[{k:variant[k] for k in
            ('key','vehicle_id','rating_configuration_id','loadout_id','ammo_id','version','mode','br')}]
        kwargs={'inventory':{'verified_arrays':1,'selected_maps':1},
                'missions':{'fixture':{'task':'cas','production_validated':True}},
                'sensitivity':{'passed':True,'catalog_sha256':digest(catalog),'config_sha256':digest(config)}}
        measured=kwargs['sensitivity']
        measured.update(kind='measured_model_search_sensitivity',policy=config['sensitivity'],thresholds=config['sensitivity']['thresholds'],cases=[{'case':name,'passed':True,
            'baseline_truncated':0,'changed_truncated':0,'boards':[{'overlap':1.,'maximum_score_delta':0.}]} for name in CASES])
        measured['sha256']=digest(measured)
        return catalog,atlas,config,world,kwargs

    def test_complete_context_can_pass_but_unrelated_evidence_cannot(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        self.assertTrue(release_gate(cat,atlas,config,world,**kwargs)['publishable'])
        world['boards']['2/all'][0]['production_mission_coverage']['cas']=0.
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertFalse(result['publishable'])
        self.assertIn('ranked_lineup_mission_evidence_incomplete',result['why_not_publishable'])

    def test_sensitivity_report_is_invalidated_by_physics_and_content_changes(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        report=kwargs['sensitivity'];report['inputs_sha256']='original-maps'
        report['sha256']=digest({k:v for k,v in report.items() if k!='sha256'})
        self.assertTrue(release_gate(cat,atlas,config,world,**kwargs,sensitivity_inputs='original-maps')['publishable'])
        self.assertFalse(release_gate(cat,atlas,config,world,**kwargs,sensitivity_inputs='changed-maps')['publishable'])
        report['cases'][0]['boards'][0]['overlap']=0
        report['sha256']=digest({k:v for k,v in report.items() if k!='sha256'})
        self.assertFalse(release_gate(cat,atlas,config,world,**kwargs,sensitivity_inputs='original-maps')['publishable'])

    def test_required_tasks_must_be_part_of_the_actual_model(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        config['spawn']['phases']=['ground','ground']
        kwargs['sensitivity']['config_sha256']=digest(config)
        kwargs['sensitivity']['sha256']=digest({k:v for k,v in kwargs['sensitivity'].items() if k!='sha256'})
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertFalse(result['publishable'])
        self.assertIn('required_tasks_not_modeled',result['why_not_publishable'])

    def test_native_anti_tank_needs_lineup_physics_not_external_mission_record(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        config['publication']['required_tasks']=['anti_tank']
        config['spawn']['phases']=['anti_tank','anti_tank']
        world['boards']['2/all'][0]['production_mission_coverage']={'anti_tank':1.}
        kwargs['missions']={};self.resign_fixture(cat,config,kwargs)
        self.assertTrue(release_gate(cat,atlas,config,world,**kwargs)['publishable'])
        world['boards']['2/all'][0]['production_mission_coverage']['anti_tank']=0.
        self.assertIn('ranked_lineup_mission_evidence_incomplete',release_gate(cat,atlas,config,world,**kwargs)['why_not_publishable'])

    def test_declared_validation_without_delivery_evidence_stays_research_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,atlas_root,ammo=fixture(root)
            config=settings();config['spawn']['phases']=['recon','recon']
            cache=ShotCache(root/'cache.sqlite')
            try:
                engine=Forecast(catalog,Atlas(atlas_root,ammo,catalog),config,cache)
                for own in engine.variants:
                    for enemy in engine.variants:
                        if own['nation']==enemy['nation']:continue
                        engine.missions[(own['key'],enemy['vehicle_id'],config['scenarios'][0]['id'],'recon')]={
                            'lower':.6,'mean':.6,'upper':.6,'status':'validated',
                            'production_validated':False,'evidence_sha256':digest([own['key'],enemy['key']])}
                keys=tuple(sorted(v['key'] for v in engine.variants if v['nation']=='a'))
                result=Optimizer(engine).evaluate(keys)
                self.assertGreater(result['evidence_coverage'],.8)
                self.assertEqual(result['production_mission_coverage']['recon'],0.)
            finally:cache.close()

    def resign_fixture(self, catalog, config, kwargs):
        report=kwargs['sensitivity'];report['catalog_sha256']=digest(catalog);report['config_sha256']=digest(config)
        report['sha256']=digest({k:v for k,v in report.items() if k!='sha256'})

    def add_fixture_aircraft(self, catalog):
        aircraft=copy.deepcopy(catalog['vehicles'][0])
        aircraft.update(id='fixture-plane',kind='aircraft',ammo=[],loadouts=[{
            'id':'clean','legal':True,'weapons':[],'source_version':'fixture',
            'source_status':'official_verified','source':'fixture'}])
        catalog['vehicles'].append(aircraft)
        return aircraft

    def test_quarantined_loadouts_are_audited_without_vetoing_valid_candidates(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        plane=self.add_fixture_aircraft(cat)
        plane['loadouts'].append({'id':'old-illegal','legal':False,'source_status':'unvalidated'})
        self.resign_fixture(cat,config,kwargs)
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertTrue(result['publishable'])
        self.assertEqual(result['source_audit']['checked_legal_loadouts'],1)
        self.assertEqual(result['source_audit']['quarantined_loadouts'],1)
        self.assertEqual(len(plane['loadouts']),2)

    def test_unsourced_legal_loadouts_still_block_even_outside_the_global_winners(self):
        cat,atlas,config,world,kwargs=self.gate_inputs();plane=self.add_fixture_aircraft(cat)
        plane['loadouts'][0]['source_status']='unvalidated'
        self.resign_fixture(cat,config,kwargs)
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertIn('loadout_source_unverified',result['why_not_publishable'])

    def test_component_is_excluded_from_independent_spawn_and_rating_audit(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        cat['vehicles'].append({'id':'launcher-part','system_role':'component'})
        self.resign_fixture(cat,config,kwargs)
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertTrue(result['publishable'])
        self.assertEqual(result['source_audit']['excluded_component_ids'],['launcher-part'])
        self.assertEqual(result['source_audit']['checked_vehicles'],1)
        member=world['boards']['2/all'][0]['members'][0]
        member['vehicle_id']='launcher-part'
        self.assertIn('ranked_configuration_not_verified',release_gate(cat,atlas,config,world,**kwargs)['why_not_publishable'])

    def test_unknown_opponent_is_retained_and_not_marked_ready(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        opponent=copy.deepcopy(cat['vehicles'][0]);opponent.update(id='unknown-rounds',nation='other',ammo=[])
        cat['vehicles'].append(opponent);self.resign_fixture(cat,config,kwargs)
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertEqual(result['source_audit']['unknown_opponent_configurations'],1)
        self.assertEqual(result['source_audit']['checked_vehicles'],2)
        variants,_=enumerate_variants(cat,atlas,config)
        unknown=next(v for v in variants if v['vehicle_id']=='unknown-rounds')
        world['boards']['2/all'][0]['members']=[{k:unknown[k] for k in
            ('key','vehicle_id','rating_configuration_id','loadout_id','ammo_id','version','mode','br')}]
        self.assertIn('ranked_configuration_not_verified',release_gate(cat,atlas,config,world,**kwargs)['why_not_publishable'])

    def test_missing_rating_source_is_not_hidden_by_unknown_configuration(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        unknown=copy.deepcopy(cat['vehicles'][0]);unknown.update(id='unknown-br',br={'current':None},br_provenance={})
        cat['vehicles'].append(unknown);self.resign_fixture(cat,config,kwargs)
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertIn('rating_version_provenance_incomplete',result['why_not_publishable'])

    def test_sourced_null_rating_still_blocks_and_names_unknown_vehicle(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        unknown=copy.deepcopy(cat['vehicles'][0]);unknown.update(id='unknown-br',br={'current':None})
        cat['vehicles'].append(unknown);self.resign_fixture(cat,config,kwargs)
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertIn('ground_rb_rating_or_eligibility_unknown',result['why_not_publishable'])
        self.assertEqual(result['source_audit']['unknown_rating_or_eligibility_ids'],['unknown-br'])

    def test_missing_dynamic_rating_option_blocks_even_with_base_rating(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        unknown=copy.deepcopy(cat['vehicles'][0]);unknown.update(id='unknown-option',
            rating_configurations={'ground_rb':{'source_version':'fixture','status':'official_verified',
                'source':{'url':'fixture'},'options':[{'id':'missing','br':{'current':None}}]}})
        cat['vehicles'].append(unknown);self.resign_fixture(cat,config,kwargs)
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertIn('ground_rb_rating_or_eligibility_unknown',result['why_not_publishable'])
        self.assertEqual(result['source_audit']['unknown_rating_or_eligibility_ids'],['unknown-option'])

    def test_unverified_dynamic_configuration_cannot_disappear_from_release_audit(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        unknown=copy.deepcopy(cat['vehicles'][0]);unknown.update(id='unverified-option',
            rating_configurations={'ground_rb':{'source_version':'fixture','status':'assumed',
                'source':{'url':'fixture'},'options':[{'id':'option','br':{'current':1.}}]}})
        cat['vehicles'].append(unknown);self.resign_fixture(cat,config,kwargs)
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertIn('rating_configuration_source_unverified',result['why_not_publishable'])

    def test_empty_dynamic_configuration_cannot_fall_back_to_base_rating(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        unknown=copy.deepcopy(cat['vehicles'][0]);unknown.update(id='empty-option',
            rating_configurations={'ground_rb':{}})
        cat['vehicles'].append(unknown);self.resign_fixture(cat,config,kwargs)
        result=release_gate(cat,atlas,config,world,**kwargs)
        self.assertIn('rating_configuration_source_unverified',result['why_not_publishable'])
        self.assertIn('ground_rb_rating_or_eligibility_unknown',result['why_not_publishable'])

    def test_payload_spawn_override_cannot_borrow_vehicle_rule_provenance(self):
        for override in (None,{'status':'assumed','source':'fixture','source_version':'fixture'},
                         {'status':'official_verified','source':'fixture','source_version':'old'}):
            with self.subTest(override=override):
                cat,atlas,config,world,kwargs=self.gate_inputs();plane=self.add_fixture_aircraft(cat)
                plane['loadouts'][0]['spawn_rule']=override;self.resign_fixture(cat,config,kwargs)
                result=release_gate(cat,atlas,config,world,**kwargs)
                self.assertIn('loadout_spawn_provenance_incomplete',result['why_not_publishable'])
        cat,atlas,config,world,kwargs=self.gate_inputs();plane=self.add_fixture_aircraft(cat)
        plane['loadouts'][0]['spawn_rule']=copy.deepcopy(plane['spawn_rule'])
        self.resign_fixture(cat,config,kwargs)
        self.assertTrue(release_gate(cat,atlas,config,world,**kwargs)['publishable'])

    def test_every_published_view_checks_selection_identity_and_task_evidence(self):
        for surface in ('views','table','category'):
            with self.subTest(surface=surface):
                cat,atlas,config,world,kwargs=self.gate_inputs()
                row=copy.deepcopy(world['boards']['2/all'][0]);row.update(lineup_key='filtered',rankable=True)
                row['members'][0]['ammo_id']='not-owned';row['production_mission_coverage']['cas']=0.
                if surface=='views':world['views']={'uptier':{'2/all':[row]}}
                else:world['tables']=[{'budget_exhausted':False,'candidates':[row] if surface=='table' else [],
                                      'category_candidates':{'combined_arms':[row]} if surface=='category' else {}}]
                result=release_gate(cat,atlas,config,world,**kwargs)
                self.assertIn('ranked_configuration_not_verified',result['why_not_publishable'])
                self.assertIn('ranked_lineup_mission_evidence_incomplete',result['why_not_publishable'])

    def test_stale_or_quarantined_configuration_cannot_enter_rankings(self):
        for field,value in [('key','unknown'),('version','old'),('mode','air_rb'),('br',2.),
                            ('ammo_id','other'),('rating_configuration_id','other'),
                            ('loadout_id','quarantined'),('loadout',{'id':'quarantined'})]:
            with self.subTest(field=field):
                cat,atlas,config,world,kwargs=self.gate_inputs()
                world['boards']['2/all'][0]['members'][0][field]=value
                self.assertIn('ranked_configuration_not_verified',release_gate(cat,atlas,config,world,**kwargs)['why_not_publishable'])

    def test_missing_members_and_ambiguous_variant_keys_cannot_pass(self):
        cat,atlas,config,world,kwargs=self.gate_inputs()
        variants,_=enumerate_variants(cat,atlas,config)
        self.assertIn('ambiguous_variant_identity',release_gate(cat,atlas,config,world,variants=variants+variants,**kwargs)['why_not_publishable'])
        world['boards']['2/all'][0].pop('members')
        self.assertIn('ranked_configuration_not_verified',release_gate(cat,atlas,config,world,**kwargs)['why_not_publishable'])

if __name__=='__main__':unittest.main()

