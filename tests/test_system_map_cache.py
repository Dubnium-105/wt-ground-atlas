import json
from pathlib import Path
import tempfile
import unittest
import numpy as np

from wt_atlas.common import file_hash
from wt_atlas.forecast.system_map_cache import SystemMapCache


class SystemMapCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'arrays').mkdir()
        class Atlas:
            def __init__(self,root):
                self.root=root;self.meta={};self.loads=0;self._loaded=None;self._loaded_id=None
                for name in ('a','b'):
                    meta=root/'arrays'/(name+'.json');meta.write_text(json.dumps({'id':name}),encoding='utf8')
                    array=root/'arrays'/(name+'.npz');array.write_bytes(('array-'+name).encode())
                    self.meta[name]={'metadata_sha256':file_hash(meta),'array_sha256':file_hash(array)}
            def verify(self,name):
                path=self.root/'arrays'/(name+'.npz')
                if file_hash(path)!=self.meta[name]['array_sha256']:raise ValueError('array changed')
                return path
            def load(self,name):
                self.verify(name);self.loads+=1;self._loaded_id=name;self._loaded={'value':np.ones(2,dtype=np.float64)}
                return self._loaded
        self.atlas=Atlas(self.root)

    def tearDown(self):self.temp.cleanup()

    def test_lru_budget_and_every_hit_revalidates_source(self):
        cache=SystemMapCache(self.atlas,16)
        first=cache.load('a');self.assertFalse(first['value'].flags.writeable)
        self.assertIs(cache.load('a'),first);self.assertEqual((self.atlas.loads,cache.hits,cache.cached_bytes),(1,1,16))
        cache.load('b');self.assertEqual(list(cache.cache),['b']);cache.load('a');self.assertEqual(self.atlas.loads,3)
        (self.root/'arrays'/'a.npz').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'array changed'):cache.load('a')

    def test_changed_metadata_and_oversized_maps_are_not_cached(self):
        cache=SystemMapCache(self.atlas,8);cache.load('a');cache.load('a')
        self.assertEqual((self.atlas.loads,cache.cached_bytes),(2,0))
        (self.root/'arrays'/'a.json').write_text('changed metadata',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'metadata checksum'):cache.load('a')


if __name__=='__main__':unittest.main()
