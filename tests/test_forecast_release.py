import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_forecast import fixture,settings,ROOT
from wt_atlas.forecast.data import canonical,read_json,validate_config
from wt_atlas.forecast.engine import ShotCache
from wt_atlas.forecast.inventory import Inventory
from wt_atlas.forecast.statistics import community_annotations
sys.path.insert(0,str(ROOT/'scripts'))
from build_product import compose
from site_lib import validate_site


class ReleaseTests(unittest.TestCase):
    def test_corrupt_well_formed_cache_recomputed_and_legacy_migrated(self):
        with tempfile.TemporaryDirectory() as t:
            c=ShotCache(Path(t)/'cache.sqlite');c.put('key',{'score':.5})
            c.db.execute('UPDATE shots SET value=? WHERE key=?',('{"score":1}', 'key'))
            self.assertIsNone(c.get('key'));c.close()

    def test_new_config_numbers_and_arbitrary_metadata_rejected(self):
        for section,key,value in [('statistics','train_fraction',True),('statistics','bootstrap_draws',0),('publication','budgets',{'file_bytes':-1})]:
            c=settings();c[section][key]=value
            with self.assertRaises(ValueError):validate_config(c)
        with tempfile.TemporaryDirectory() as t:
            r=Path(t);cat,atlas,ammo=fixture(r);(atlas/'arrays/bad.json').write_text('[1,2,3]')
            idx=Inventory(r/'index.sqlite');result=idx.sync(atlas,ammo,cat)
            self.assertEqual(result['selected_maps'],16);self.assertEqual(result['rejections']['Metadata must be an object'],1);idx.close()

    def test_local_br_change_preserves_physics_but_one_profile_invalidates_only_its_maps(self):
        with tempfile.TemporaryDirectory() as t:
            r=Path(t);cat,atlas,ammo=fixture(r);idx=Inventory(r/'index.sqlite');idx.sync(atlas,ammo,cat)
            cat['vehicles'][0]['br']['current']=9.7
            self.assertEqual(idx.sync(atlas,ammo,cat)['metrics']['incremental_hits'],16)
            profiles=read_json(ammo);profiles[0]['distance_m']=100;ammo.write_text(canonical(profiles))
            result=idx.sync(atlas,ammo,cat);self.assertEqual(result['metrics']['incremental_hits'],12);idx.close()

    def test_community_is_qualitative_and_not_vote_weighted(self):
        p={k:'fixture' for k in ('source','sampling_window','game_version','deduplication','bias_notes','authorization')}
        p.update(mode='ground_rb',sample_count=1,annotations=[{'vehicle_id':'a','source_url':'https://example.invalid/review','labels':['maneuverable'],'disputes':['map dependence'],'votes':99999}])
        r=community_annotations(p,{'a'});self.assertFalse(r['used_for_scoring']);self.assertNotIn('votes',r['annotations'][0])

    def test_nine_profiles_atomic_composition_and_corrupt_child_shard(self):
        with tempfile.TemporaryDirectory() as t:
            r=Path(t);cat,atlas,ammo=fixture(r);cfg=settings();cfg['skill_profiles']={k:{**v,'nodes':1,'sd':0} for k,v in cfg['skill_profiles'].items()}
            (r/'config.json').write_text(canonical(cfg))
            run=subprocess.run([sys.executable,'scripts/build_forecast.py','--catalog',str(r/'catalog.json'),'--ammunition',str(ammo),'--atlas',str(atlas),'--config',str(r/'config.json'),'--index',str(r/'index.sqlite'),'--cache',str(r/'cache.sqlite'),'--out',str(r/'forecast'),'--all-profiles'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(run.returncode,0,run.stderr)
            index=read_json(r/'forecast/index.json');self.assertEqual(len(index['cohorts']),9)
            product=r/'site';result=compose(r/'forecast',product)
            self.assertLess(result['static_total_bytes'],10*1024**2)
            self.assertIn('研究快照未提供',(product/'research/index.html').read_text(encoding='utf-8'))
            before=(product/'index.json').read_bytes()
            child=r/'forecast/profiles/expert-balanced';ci=read_json(child/'index.json');(child/ci['report']['url']).write_bytes(b'{}')
            with self.assertRaises(ValueError):compose(r/'forecast',product)
            self.assertEqual(before,(product/'index.json').read_bytes())


if __name__=='__main__':unittest.main()
