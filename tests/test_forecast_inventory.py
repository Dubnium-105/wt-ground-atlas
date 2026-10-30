import copy
import json
from pathlib import Path
import tempfile
import unittest
from test_forecast import fixture, settings
from wt_atlas.forecast.data import Atlas, canonical, digest, file_hash
from wt_atlas.forecast.inventory import Inventory, bind_geometry, coverage


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.catalog,self.atlas,self.ammo=fixture(self.root)
        self.index=Inventory(self.root/'index.sqlite')

    def tearDown(self):
        self.index.close();self.temp.cleanup()

    def test_incremental_and_array_corruption(self):
        first=self.index.sync(self.atlas,self.ammo,self.catalog)
        self.assertEqual(first['selected_maps'],16)
        second=self.index.sync(self.atlas,self.ammo,self.catalog)
        self.assertEqual(second['metrics']['incremental_hits'],16)
        verified=self.index.verify(self.atlas,max_workers=2,memory_budget=128*1024**2)
        self.assertEqual(verified['verified_arrays'],16)
        path=next((self.atlas/'arrays').glob('*.npz'));path.write_bytes(b'bad')
        self.index.sync(self.atlas,self.ammo,self.catalog)
        result=self.index.verify(self.atlas,resume=True)
        self.assertEqual(result['verified_arrays'],15)
        self.assertEqual(result['rejections']['NPZ checksum failure'],1)

    def test_bad_profile_does_not_abort_other_maps(self):
        profiles=json.loads(self.ammo.read_text());profiles[0]['distance_m']=100
        self.ammo.write_text(canonical(profiles),encoding='utf-8')
        result=self.index.sync(self.atlas,self.ammo,self.catalog)
        self.assertEqual(result['selected_maps'],12)
        self.assertEqual(result['rejections']['profile_hash_mismatch'],4)

    def test_claimed_hash_is_not_independent_geometry(self):
        self.index.sync(self.atlas,self.ammo,self.catalog)
        self.assertEqual({m['target_binding'] for m in self.index.metadata.values()},{'source_declared_id_version_only'})

    def test_disk_mapping_and_coverage(self):
        self.index.sync(self.atlas,self.ammo,self.catalog)
        atlas=Atlas(self.atlas,self.ammo,self.catalog,index=self.index)
        self.assertEqual(atlas.loads,0)
        report=coverage(self.catalog,atlas,settings(),self.index)
        self.assertEqual(report['counts']['available_bidirectional_pairs'],8)
        self.assertIn('arrays_not_fully_verified',report['blockers'])
        atlas.load(next(iter(atlas.meta)))
        self.assertEqual(atlas.loads,1)

    def test_duplicate_identity_is_quarantined_and_removal_resumes(self):
        path=next((self.atlas/'arrays').glob('*.json'))
        m=json.loads(path.read_text());m['map_id']='other'
        extra=path.with_name('other.json');extra.write_text(canonical(m),encoding='utf-8')
        r=self.index.sync(self.atlas,self.ammo,self.catalog)
        self.assertEqual(r['ambiguous_maps'],2);self.assertEqual(r['selected_maps'],15)
        extra.unlink();r=self.index.sync(self.atlas,self.ammo,self.catalog)
        self.assertEqual(r['selected_maps'],16)

    def test_independent_geometry_chain_and_mutation(self):
        jobs=self.root/'jobs';jobs.mkdir()
        v=self.catalog['vehicles'][0];identifier=v['id']
        raw=self.root/'raw.grp';raw.write_bytes(b'fixture geometry')
        layer=self.root/'layer.npz';layer.write_bytes(b'fixture layers')
        profile={'vehicle':identifier,'provenance':{'client_version':'synthetic-only','datamine_commit':'synthetic'}}
        prof=self.root/'profile.json';prof.write_text(canonical(profile))
        meta={'grp_sha256':file_hash(raw),'arrays_sha256':file_hash(layer),'plan':{'angles_deg':[0.]}}
        (self.root/'layer.json').write_text(canonical(meta))
        v['source_attributes']['client'].update(geometry_sha256=file_hash(raw),configuration_sha256=file_hash(prof))
        job={'vehicle_id':identifier,'status':'complete','profile_path':prof.name,'layer_metadata':'layer.json','raw_path':raw.name,'profile_sha256':file_hash(prof),'grp_sha256':file_hash(raw)}
        (jobs/'job.json').write_text(canonical(job))
        bound,errors=bind_geometry(self.root,jobs,self.catalog)
        self.assertFalse(errors);self.assertIn(identifier,bound)
        raw.write_bytes(b'changed geometry')
        bound,errors=bind_geometry(self.root,jobs,self.catalog)
        self.assertFalse(bound);self.assertIn('hash mismatch',errors[0]['error'])


if __name__=='__main__':unittest.main()
