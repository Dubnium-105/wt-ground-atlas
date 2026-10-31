import copy
from pathlib import Path
import tempfile
import unittest
from test_forecast_range_rebuild import source_fixture,profile
from wt_atlas.common import read_json,write_json,file_hash
from wt_atlas.native_rebuild import rebuild,plan
from wt_atlas.forecast.inventory import Inventory


class NativeRebuildTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.repo=self.root/'source';self.catalog,self.ammo,self.layers,self.raw=source_fixture(self.repo)
        catalog=read_json(self.catalog);catalog['vehicles'][0].update(nation='fixture',ammo=[{'id':'fixture::round'}])
        write_json(self.catalog,catalog)
        self.request=self.root/'requests.json';self.output=self.root/'generated';self.write_request()
    def tearDown(self):self.temp.cleanup()
    def write_request(self,profiles=('fixture_round',)):
        write_json(self.request,{'catalog_sha256':file_hash(self.catalog),'ammunition_sha256':file_hash(self.ammo),
            'candidate_rebuild_requests':[{'target_vehicle_id':'fixture','profile_id':p,'attacker_ids':['fixture']} for p in profiles]})
    def run_rebuild(self,**kw):return rebuild(self.repo,self.catalog,self.ammo,self.request,self.output,**kw)

    def test_native_output_roundtrip_resume_and_source_preservation(self):
        inputs=[self.catalog,self.ammo,self.layers,self.layers.with_suffix('.npz'),self.raw]
        hashes=[file_hash(p) for p in inputs]
        result=self.run_rebuild();self.assertEqual(result['computed_maps'],1)
        self.assertEqual(result['maps'][0]['distance_m'],10)
        self.assertEqual(result['source_probability_maps_read'],0)
        self.assertEqual(read_json(self.output/'ammunition.json'),read_json(self.ammo))
        index=Inventory(self.root/'generated.sqlite')
        try:
            self.assertEqual(index.sync(self.output,self.ammo,read_json(self.catalog))['selected_maps'],1)
            self.assertEqual(index.verify(self.output)['verified_arrays'],1)
        finally:index.close()
        self.assertEqual(self.run_rebuild(resume=True)['reused_maps'],1)
        next((self.output/'arrays').glob('*.npz')).write_bytes(b'broken')
        self.assertEqual(self.run_rebuild(resume=True)['computed_maps'],1)
        self.assertEqual(hashes,[file_hash(p) for p in inputs])

    def test_changed_input_and_unowned_profile_are_rejected(self):
        original=read_json(self.request);wrong=copy.deepcopy(original)
        wrong['catalog_sha256']='0'*64;write_json(self.request,wrong)
        with self.assertRaisesRegex(ValueError,'hash mismatch'):self.run_rebuild()
        wrong=copy.deepcopy(original);wrong['candidate_rebuild_requests'][0]['attacker_ids']=['other']
        write_json(self.request,wrong)
        with self.assertRaisesRegex(ValueError,'exactly owned'):self.run_rebuild()

    def test_missing_distance_reference_and_derived_range_are_rejected(self):
        original=read_json(self.ammo)
        for field,value in [('distance_m',None),('reference_only',True),('impact_distance_m',100)]:
            changed=copy.deepcopy(original);changed[0][field]=value;write_json(self.ammo,changed);self.write_request()
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'native-distance'):self.run_rebuild()

    def test_geometry_mutation_cannot_reuse_cached_output(self):
        self.run_rebuild();self.raw.write_bytes(b'mutated source')
        with self.assertRaisesRegex(ValueError,'geometry unavailable'):self.run_rebuild(resume=True)

    def test_unavailable_yaw_and_mislabeled_range_are_rejected(self):
        request=read_json(self.request);request['candidate_rebuild_requests'][0]['required_yaws_deg']=[15]
        write_json(self.request,request)
        with self.assertRaisesRegex(ValueError,'source yaw unavailable'):self.run_rebuild()
        p=read_json(self.ammo);p[0]['distance_m']=500;write_json(self.ammo,p);self.write_request()
        with self.assertRaisesRegex(ValueError,'reference source curve'):self.run_rebuild()

    def test_limit_resumes_new_pairs_and_source_directory_is_protected(self):
        second=profile();second['name']='fixture_round_2';write_json(self.ammo,[profile(),second])
        self.write_request(('fixture_round','fixture_round_2'))
        first=self.run_rebuild(limit=1,resume=True);self.assertEqual(first['pending_maps'],1)
        second=self.run_rebuild(limit=1,resume=True)
        self.assertEqual(second['computed_maps'],1);self.assertEqual(second['reused_maps'],1);self.assertEqual(second['pending_maps'],0)
        with self.assertRaisesRegex(ValueError,'outside the source'):
            rebuild(self.repo,self.catalog,self.ammo,self.request,self.repo/'output')

    def test_planner_discovers_exact_missing_enemy_pair_without_human_lineup(self):
        from test_forecast import fixture,settings
        root=self.root/'world';root.mkdir();catalog,atlas,ammo=fixture(root)
        config=root/'config.json';write_json(config,settings())
        index=Inventory(root/'inventory.sqlite')
        try:
            index.sync(atlas,ammo,catalog)
            with self.assertRaisesRegex(ValueError,'Finish inventory'):plan(root/'catalog.json',ammo,root/'inventory.sqlite',config)
            index.verify(atlas)
            self.assertEqual(plan(root/'catalog.json',ammo,root/'inventory.sqlite',config)['candidate_request_count'],0)
            attacker=catalog['vehicles'][0]['id'];target=catalog['vehicles'][-1]['id']
            for path in (atlas/'arrays').glob('*.json'):
                row=read_json(path)
                if row['target_id']==target and row['ammo_id']==attacker+'_profile':path.unlink()
            index.sync(atlas,ammo,catalog);index.verify(atlas)
            result=plan(root/'catalog.json',ammo,root/'inventory.sqlite',config)
            self.assertEqual(result['candidate_rebuild_requests'],[{'target_vehicle_id':target,'profile_id':attacker+'_profile','attacker_ids':[attacker],'required_yaws_deg':[0.]}])
        finally:index.close()

if __name__=='__main__':unittest.main()
