"""Verify sampled export preserves target ownership, paths and unknown regions."""
from pathlib import Path
import tempfile
import unittest
import numpy as np
from test_pipeline import material, make_layers, CONFIG
from wt_atlas.common import read_json, write_json
from scripts.build_target_layers import sample_target

class TargetLayerContracts(unittest.TestCase):
    def fixture(self,directory):
        plate=material('armor',100)
        for values in plate['loaded_class']['armor']['arrays'].values():values['values']*=11
        plate['loaded_class']['armor']['arrays']['ArmorQuality']['values'][10]=2
        # Exposed target, ordinary plate, unknown layer, and armor behind target.
        rows=[(0,2,2,2.1,1,np.inf),(1,0,1,1.1,.5,np.inf),(1,2,2,2.1,1,np.inf),
              (2,1,1,1.1,1,np.inf),(2,2,2,2.1,1,np.inf),(3,2,1,1.1,1,np.inf),(3,0,2,2.1,1,np.inf)]
        meta=make_layers(directory,rows,[plate,material('unknown'),material('protected')],4)
        job=directory/'job.json'
        write_json(job,{'vehicle_id':'fixture-target','layer_metadata':str(meta),'grp_sha256':'fixture-grp','profile_sha256':'fixture-config'})
        return job,meta

    def test_mechanisms_and_unknown_are_kept_distinct_and_only_pre_target_armor_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,_=self.fixture(Path(tmp));result=sample_target(job,CONFIG,64)
            self.assertEqual(result['target_id'],'fixture-target')
            self.assertEqual(result['geometry_sha256'],'fixture-grp')
            view=result['views'][0];self.assertEqual(view['protected_rays'],4)
            self.assertEqual(view['rays'][0],[])
            layer=view['rays'][1][0]
            self.assertEqual(layer[0],100);self.assertEqual(layer[1],200)
            self.assertEqual(layer[4],200);self.assertEqual(layer[5],400)
            self.assertEqual(layer[8],.5);self.assertIsNone(view['rays'][2]);self.assertEqual(view['rays'][3],[])
            self.assertEqual(sample_target(job,CONFIG,64),result)

    def test_layer_cache_hash_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,meta=self.fixture(Path(tmp));data=read_json(meta);data['arrays_sha256']='wrong';write_json(meta,data)
            with self.assertRaisesRegex(ValueError,'Layer hash mismatch'):sample_target(job,CONFIG,64)
