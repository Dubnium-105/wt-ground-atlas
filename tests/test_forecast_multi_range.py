"""One selected round can have several exact physical-distance datasets."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from test_forecast import fixture, settings
from wt_atlas.forecast.data import Atlas,canonical,digest
from wt_atlas.forecast.engine import Forecast,ShotCache,enumerate_variants

class MultiRangeTests(unittest.TestCase):
    def fixture(self,root):
        catalog,atlas_root,ammo=fixture(root)
        profiles=json.loads(ammo.read_text());derived=[]
        for original in profiles:
            p=copy.deepcopy(original);p.update(name=p['name']+'_500',distance_m=500.)
            derived.append(p)
            for path in list((atlas_root/'arrays').glob('*.json')):
                meta=json.loads(path.read_text())
                if meta['ammo_id']!=original['name']:continue
                meta.update(map_id=digest([meta['map_id'],500]),ammo_id=p['name'],ammo_profile_sha256=digest(p))
                (path.parent/(meta['map_id']+'.json')).write_text(canonical(meta))
        ammo.write_text(canonical(profiles+derived))
        return catalog,Atlas(atlas_root,ammo,catalog),ammo

    def test_native_distances_do_not_duplicate_loadouts_or_opponent_mass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,atlas,ammo=self.fixture(root)
            cache=ShotCache(root/'cache.sqlite')
            try:
                engine=Forecast(catalog,atlas,settings(),cache)
                self.assertEqual(len(engine.variants),4)
                self.assertEqual(len(engine.by_key),4)
                opponents=engine.opponents('a',28,'ground')
                self.assertEqual(len(opponents),2)
                self.assertAlmostEqual(sum(w for k,w in opponents),1.)
                a=next(v for v in engine.variants if v['nation']=='a')
                b=next(v for v in engine.variants if v['nation']=='b')
                s=settings()['scenarios'][0]
                near=engine.shot(a,b,{**s,'physical_distance_m':10},.5)
                far=engine.shot(a,b,{**s,'physical_distance_m':500},.5)
                self.assertEqual(near['physical_distance_m'],10)
                self.assertEqual(far['physical_distance_m'],500)
                self.assertNotEqual(near['aim_cache_key'],far['aim_cache_key'])
                self.assertIsNone(engine.shot(a,b,{**s,'physical_distance_m':300},.5))
            finally:cache.close()

    def test_same_ammo_same_distance_conflict_fails_without_first_match_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,atlas,ammo=self.fixture(root)
            p=copy.deepcopy(next(iter(atlas.profiles.values())));p['name']='conflicting_same_range'
            atlas._binding_index[p['provenance']['penetration_source_ammo_ids'][0]].append(p)
            with self.assertRaisesRegex(ValueError,'Ambiguous ammunition distance'):
                enumerate_variants(catalog,atlas,settings())

if __name__=='__main__':unittest.main()
