"""Optional records use auditable priors without inventing physical evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_forecast import fixture, settings, ROOT
import test_forecast_release_evidence as release_fixtures
from wt_atlas.forecast.data import Atlas, digest, validate_config
from wt_atlas.forecast.engine import Forecast, ShotCache
from wt_atlas.forecast.search import Optimizer
from wt_atlas.forecast.versions import release_gate


def defaults():
    return json.loads((ROOT/'config/forecast.json').read_text(encoding='utf-8'))['mission_policy']


class MissionDefaultTests(unittest.TestCase):
    def test_defaults_remain_versioned_ordered_assumptions(self):
        for change in ({'status':'validated'}, {'schema_version':'unknown'}, {'mode':'automatic'},
                       {'default_interval':{'lower':0,'mean':True,'upper':1}},
                       {'default_interval':{'lower':.8,'mean':.5,'upper':1}}):
            c=settings();c['mission_policy']={**defaults(),**change}
            with self.subTest(change=change), self.assertRaises(ValueError):validate_config(c)

    def test_defaults_leave_ground_results_and_observation_counts_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,atlas_root,ammo=fixture(root);cache=ShotCache(root/'cache.sqlite')
            try:
                c=settings();strict=Forecast(catalog,Atlas(atlas_root,ammo,catalog),c,cache)
                c=copy.deepcopy(c);c['mission_policy']=defaults()
                assumed=Forecast(catalog,strict.atlas,c,cache)
                v=assumed.variants[0];context=(v['key'],'ground',v['br_step'],c['scenarios'][0]['id'])
                for field in ('mean','lower','upper','coverage','production_coverage','assumed_coverage'):
                    self.assertEqual(strict.mission(*context)[field],assumed.mission(*context)[field])
                row=assumed.mission(v['key'],'recon',v['br_step'],c['scenarios'][0]['id'])
                self.assertEqual((row['lower'],row['mean'],row['upper']),(0.,.5,1.))
                self.assertEqual((row['coverage'],row['production_coverage'],row['assumed_coverage']),(0.,0.,1.))
                trace=cache.evidence_get(row['evidence_trace']['pair_keys'][0])['value']
                self.assertEqual(trace['source'],'configured_prior_not_observation')
                self.assertEqual(trace['policy_sha256'],digest(c['mission_policy']))
            finally:cache.close()

    def test_supplied_versioned_records_override_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,atlas_root,ammo=fixture(root);cache=ShotCache(root/'cache.sqlite')
            try:
                c=settings();c['mission_policy']=defaults();atlas=Atlas(atlas_root,ammo,catalog)
                e=Forecast(catalog,atlas,c,cache);own=e.variants[0]
                records=[dict(variant_key=own['key'],target_vehicle_id=v['vehicle_id'],scenario_id=c['scenarios'][0]['id'],
                    task='recon',lower=.6,mean=.7,upper=.8,source_version='synthetic-only',source='synthetic test observation',status='validated')
                    for v in e.variants if v['nation']!=own['nation']]
                path=root/'missions.json';path.write_text(json.dumps({'records':records}),encoding='utf-8')
                e=Forecast(catalog,atlas,c,cache,missions_path=path)
                row=e.mission(own['key'],'recon',own['br_step'],c['scenarios'][0]['id'])
                self.assertAlmostEqual(row['mean'],.7);self.assertEqual(row['coverage'],1.)
                self.assertEqual(row['assumed_coverage'],0.);self.assertEqual(row['production_coverage'],0.)
                records[0]['source_version']='other-version';path.write_text(json.dumps({'records':records}),encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'version mismatch'):Forecast(catalog,atlas,c,cache,missions_path=path)
            finally:cache.close()

    def test_default_task_can_be_used_without_bypassing_ground_physics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,atlas_root,ammo=fixture(root);cache=ShotCache(root/'cache.sqlite')
            try:
                c=settings();c['mission_policy']=defaults();c['spawn']['phases']=['recon','recon']
                atlas=Atlas(atlas_root,ammo,catalog);e=Forecast(catalog,atlas,c,cache)
                keys=tuple(sorted(v['key'] for v in e.variants if v['nation']=='a'))
                row=Optimizer(e).evaluate(keys)
                self.assertTrue(row['rankable']);self.assertEqual(row['evidence_coverage'],0.)
                self.assertEqual(row['assumed_mission_coverage'],1.);self.assertEqual(row['physical_pair_coverage'],1.)
                self.assertEqual(row['status'],'assumed_mission_prediction')
                self.assertTrue(row['uncertainty']['includes_mission_assumptions'])
                # A missing reverse/forward physical map cannot be filled by task defaults.
                atlas.map_for=lambda *args:None
                e=Forecast(catalog,atlas,c,cache);row=Optimizer(e).evaluate(keys)
                self.assertFalse(row['rankable']);self.assertIsNone(row['score'])
                self.assertEqual(row['physical_pair_coverage'],0.)
            finally:cache.close()

    def test_policy_change_invalidates_lineup_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,atlas_root,ammo=fixture(root);cache=ShotCache(root/'cache.sqlite')
            try:
                c=settings();c['mission_policy']=defaults();c['spawn']['phases']=['recon','recon']
                e=Forecast(catalog,Atlas(atlas_root,ammo,catalog),c,cache)
                keys=tuple(sorted(v['key'] for v in e.variants if v['nation']=='a'))
                before=Optimizer(e).evaluate(keys)
                c=copy.deepcopy(c);c['mission_policy']['default_interval']['mean']=.4
                after=Optimizer(Forecast(catalog,e.atlas,c,cache)).evaluate(keys)
                self.assertNotEqual(before['lineup_key'],after['lineup_key'])
                self.assertNotEqual(before['estimate'],after['estimate'])
            finally:cache.close()

    def test_missing_records_are_optional_only_under_explicit_default_policy(self):
        cat,atlas,c,world,kw=release_fixtures.ReleaseEvidenceTests().gate_inputs()
        kw['missions']={};world['boards']['2/all'][0]['production_mission_coverage']['cas']=0.
        strict=release_gate(cat,atlas,c,world,**kw)
        self.assertIn('full_mode_mission_evidence_missing',strict['why_not_publishable'])
        c['mission_policy']=defaults()
        kw['sensitivity']['config_sha256']=digest(c)
        kw['sensitivity']['sha256']=digest({k:v for k,v in kw['sensitivity'].items() if k!='sha256'})
        result=release_gate(cat,atlas,c,world,**kw)
        self.assertTrue(result['publishable']);self.assertFalse(result['mission_evidence_required'])
        self.assertEqual(result['calibration_status'],'uncalibrated');self.assertFalse(result['empirical_win_rate_validated'])
        atlas.meta['map']['source_version']='other'
        self.assertIn('snapshot_physics_version_mismatch',release_gate(cat,atlas,c,world,**kw)['why_not_publishable'])


if __name__=='__main__':unittest.main()
