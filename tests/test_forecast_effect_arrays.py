from pathlib import Path
import tempfile
import unittest
import numpy as np
from test_forecast import fixture,settings
from wt_atlas.forecast.data import Atlas,canonical,file_hash
from wt_atlas.forecast.engine import Forecast,ShotCache


class SpatialEffectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cat,self.aroot,self.ammo=fixture(self.root);self.config=settings()
        # Two exact native angle slices, so an invalid unrequested angle is tested.
        self.map_path=next((self.aroot/'arrays').glob('*.npz'))
        with np.load(self.map_path,allow_pickle=False) as packet:data={k:packet[k] for k in packet.files}
        for key in ('penetration_probability','status','material_response_flags'):data[key]=np.repeat(data[key],2,axis=0)
        data['angles_deg']=np.array([0.,15.]);np.savez_compressed(self.map_path,**data)
        import json
        metadata=self.map_path.with_suffix('.json');self.meta=json.loads(metadata.read_text(encoding='utf-8'))
        self.meta['array_sha256']=file_hash(self.map_path);metadata.write_text(canonical(self.meta),encoding='utf-8')
        self.atlas=Atlas(self.aroot,self.ammo,self.cat);self.cache=ShotCache(self.root/'shots.sqlite')

    def tearDown(self):self.cache.close();self.temp.cleanup()

    def engine(self,array,**changes):
        path=self.root/'effects.npz';np.savez_compressed(path,conditional_outcomes=array)
        row={'map_id':self.meta['map_id'],'map_array_sha256':self.meta['array_sha256'],
             'source_version':'synthetic-only','source':'synthetic controlled fixture','status':'validated',
             'array_file':path.name,'array_sha256':file_hash(path),**changes}
        manifest=self.root/'effects.json';manifest.write_text(canonical({'records':[row]}),encoding='utf-8')
        return Forecast(self.cat,self.atlas,self.config,self.cache,manifest)

    def test_full_spatial_shape_and_source_version_are_required(self):
        valid=np.full((2,20,20,3),.1,np.float32)
        engine=self.engine(valid);result,_=engine._effect(self.meta['map_id'],(20,20),0)
        np.testing.assert_array_equal(result,valid[0])
        for wrong in (valid[:1],valid[:,:,:19],np.zeros((2,20,20,3),np.uint8)):
            with self.assertRaisesRegex(ValueError,'layout'):self.engine(wrong)._effect(self.meta['map_id'],(20,20),0)
        with self.assertRaisesRegex(ValueError,'version'):self.engine(valid,source_version='other')._effect(self.meta['map_id'],(20,20),0)

    def test_invalid_mass_in_unrequested_angle_is_not_hidden(self):
        for invalid in (float('nan'),-.1,1.1):
            array=np.full((2,20,20,3),.1,np.float32);array[1,0,0,0]=invalid
            with self.assertRaisesRegex(ValueError,'probability'):self.engine(array)._effect(self.meta['map_id'],(20,20),0)

    def test_effects_obey_same_expanded_memory_budget(self):
        array=np.full((2,20,20,3),.1,np.float32);engine=self.engine(array)
        self.atlas.memory_budget=512
        with self.assertRaisesRegex(ValueError,'budget'):engine._effect(self.meta['map_id'],(20,20),0)
