import copy
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from test_forecast import fixture,settings
from wt_atlas.forecast.engine import Forecast,ShotCache
from wt_atlas.forecast.data import Atlas,canonical,digest,file_hash
from wt_atlas.forecast.inventory import Inventory
from wt_atlas.forecast.profile_history import annotation_equivalence,ProfileHistory


class ProfileHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.catalog,self.atlas,self.ammo=fixture(self.root)
        self.before=json.loads(self.ammo.read_text(encoding='utf-8'))
        self.history_file=self.root/'history.json';self.history_file.write_text(canonical(self.before))
        self.after=copy.deepcopy(self.before)
        self.after[0]['provenance'].update(catalog_physical_mismatches={},v2_equivalent_candidates=[])
        self.ammo.write_text(canonical(self.after))

    def tearDown(self):self.temp.cleanup()

    def test_exact_historical_hash_and_annotation_changes_preserve_npz(self):
        history=ProfileHistory([self.history_file]);before_hashes={p.name:file_hash(p) for p in (self.atlas/'arrays').iterdir()}
        with self.assertRaises(ValueError):Atlas(self.atlas,self.ammo,self.catalog)
        atlas=Atlas(self.atlas,self.ammo,self.catalog,profile_history=history)
        reconciled=[m for m in atlas.meta.values() if m.get('profile_reconciliation')]
        self.assertEqual(len(reconciled),4)
        self.assertEqual({m['ammo_profile_sha256'] for m in reconciled},{digest(self.before[0])})
        self.assertEqual({m['profile_reconciliation']['active_profile_sha256'] for m in reconciled},{digest(self.after[0])})
        for m in reconciled:atlas.load(m['map_id'])
        self.assertEqual(before_hashes,{p.name:file_hash(p) for p in (self.atlas/'arrays').iterdir()})

    def test_no_parameter_binding_assumption_or_version_can_change(self):
        for field,value in [('distance_m',100),('penetration_mm',50),('mode','CE'),('reference_only',True),('new_unknown_parameter',1)]:
            after=copy.deepcopy(self.after[0]);after[field]=value
            with self.subTest(field=field):self.assertFalse(annotation_equivalence(self.before[0],after))
        for field,value in [('client_version','other'),('datamine_commit','other'),('penetration_source_ammo_ids',['different::round']),
                            ('source_weapon_sha256','a'*64),('model_assumptions',{'unknown':'guessed'}),
                            ('catalog_physical_mismatches',{'mass':'different'}),('v2_equivalent_candidates',['ambiguous'])]:
            after=copy.deepcopy(self.after[0]);after['provenance'][field]=value
            with self.subTest(field=field):self.assertFalse(annotation_equivalence(self.before[0],after))

    def test_only_two_exact_binding_descriptions_can_be_reconciled(self):
        before=copy.deepcopy(self.before[0]);after=copy.deepcopy(before)
        before['provenance']['source_match_method']='exact_projectile_name_and_physical_fields'
        after['provenance']['source_match_method']='exact_weapon_mount_and_unique_physical_fields'
        self.assertTrue(annotation_equivalence(before,after))
        after['provenance']['source_match_method']='similar_name_or_caliber'
        self.assertFalse(annotation_equivalence(before,after))

    def test_index_cache_invalidates_if_history_is_changed_or_removed(self):
        index=Inventory(self.root/'index.sqlite')
        try:
            self.assertEqual(index.sync(self.atlas,self.ammo,self.catalog)['selected_maps'],12)
            history=ProfileHistory([self.history_file])
            report=index.sync(self.atlas,self.ammo,self.catalog,profile_history=history)
            self.assertEqual(report['selected_maps'],16);self.assertEqual(report['annotation_reconciled_maps'],4)
            report=index.sync(self.atlas,self.ammo,self.catalog,profile_history=history)
            self.assertEqual(report['metrics']['incremental_hits'],16)
            changed=copy.deepcopy(self.before);changed[0]['distance_m']=100
            self.history_file.write_text(canonical(changed))
            report=index.sync(self.atlas,self.ammo,self.catalog,profile_history=ProfileHistory([self.history_file]))
            self.assertEqual(report['selected_maps'],12)
            self.assertEqual(index.sync(self.atlas,self.ammo,self.catalog)['selected_maps'],12)
        finally:index.close()

    def test_forged_metadata_receipt_cannot_replace_history(self):
        for p in (self.atlas/'arrays').glob('*.json'):
            m=json.loads(p.read_text());m['profile_reconciliation']={'physical_and_ownership_fields_unchanged':True}
            p.write_text(canonical(m))
        with self.assertRaises(ValueError):Atlas(self.atlas,self.ammo,self.catalog)
        history=ProfileHistory([self.history_file]);self.assertEqual(len(Atlas(self.atlas,self.ammo,self.catalog,profile_history=history).meta),16)
        with self.assertRaises(ValueError):history.reconcile('f'*64,self.after[0])

    def test_shot_values_are_unchanged_and_trace_keeps_both_full_hashes(self):
        history=ProfileHistory([self.history_file]);cache=ShotCache(self.root/'shots.sqlite')
        try:
            old=Forecast(self.catalog,Atlas(self.atlas,self.history_file,self.catalog),settings(),cache)
            new=Forecast(self.catalog,Atlas(self.atlas,self.ammo,self.catalog,profile_history=history),settings(),cache)
            attacker_id=self.catalog['vehicles'][0]['id'];target_id=self.catalog['vehicles'][-1]['id']
            choose=lambda engine,identifier:next(v for v in engine.variants if v['vehicle_id']==identifier)
            first=old.shot(choose(old,attacker_id),choose(old,target_id),settings()['scenarios'][0],0.)
            after=new.shot(choose(new,attacker_id),choose(new,target_id),settings()['scenarios'][0],0.)
            self.assertIsNotNone(first);self.assertIsNotNone(after)
            for k,value in first.items():
                if isinstance(value,(int,float)):self.assertEqual(value,after[k],k)
            self.assertNotEqual(first['aim_cache_key'],after['aim_cache_key'])
            self.assertEqual(after['source_profile_sha256'],digest(self.before[0]))
            self.assertEqual(after['active_profile_sha256'],digest(self.after[0]))
            self.assertTrue(after['profile_reconciliation']['physical_and_ownership_fields_unchanged'])
        finally:cache.close()

    def test_build_cli_carries_history_into_index_validation_and_gate(self):
        config=self.root/'config.json';config.write_text(canonical(settings()))
        run=subprocess.run([sys.executable,'scripts/build_forecast.py','--catalog',str(self.root/'catalog.json'),
            '--ammunition',str(self.ammo),'--atlas',str(self.atlas),'--profile-history',str(self.history_file),
            '--config',str(config),'--index',str(self.root/'build.sqlite'),'--cache',str(self.root/'build-shots.sqlite'),
            '--preflight','--max-workers','1','--out',str(self.root/'site'),'--reports',str(self.root/'reports')],
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(run.returncode,0,run.stderr)
        result=json.loads(run.stdout)
        self.assertEqual(result['maps'],16)
        self.assertFalse(result['release_readiness']['current']['publishable'])
        validation=json.loads((self.root/'reports/forecast-validation.json').read_text(encoding='utf-8'))
        self.assertEqual(validation['inputs']['profile_history'],ProfileHistory([self.history_file]).signature)
        self.assertEqual(validation['coverage']['inventory']['annotation_reconciled_maps'],4)

if __name__=='__main__':unittest.main()
