"""Native range inputs are source points; no precomputed probability is reused."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from test_forecast import ROOT
from test_pipeline import CONFIG, make_layers, material
from wt_atlas.common import read_json,write_json,file_hash
from wt_atlas.cpu_model import PreparedLayers
from wt_atlas.range_rebuild import source_range_profile,rebuild

def profile():
    return {'name':'fixture_round','mode':'KE','penetration_mm':100.,'dispersion':.1,
        'damage_caliber_mm':10.,'mechanism_id':0,'air_loss_mm_per_m':0.,
        'slope_rows':[],'ricochet_rows':[],'distance_m':10.,
        'normalized_distance_curve':[[10.,1.],[100.,.9],[500.,.5],[1000.,.3]],
        'provenance':{'client_version':'fixture','datamine_commit':'a'*40,
                      'penetration_source_ammo_ids':['fixture::round']}}


def ce_profile(source_root):
    source=Path(source_root)/'gamedata/fixture.blk';source.parent.mkdir(parents=True,exist_ok=True)
    source.write_text('bullet {\n bulletName:t = "fixture_heat"\n bulletType:t = "heat_tank"\n cumulativeDamage {\n armorPower:r = 100\n }\n}\n',encoding='utf-8')
    p=profile();p.update(mode='CE',distance_m=None,normalized_distance_curve=[])
    p['provenance'].update(bullet_name='fixture_heat',bullet_type='heat_tank',
        source_weapon_path='gamedata/fixture.blk',source_weapon_sha256=file_hash(source))
    return p,source

def source_fixture(root):
    data=root/'data/work/client';layers=data/'geometry';layers.mkdir(parents=True)
    rows=[(0,0,.7,.71,1.,np.inf),(0,1,2.,2.1,1.,np.inf)]
    metadata=make_layers(layers,rows,[material('armor',80),material('protected')],1)
    meta=read_json(metadata);meta['plan'].update(world_width_m=.04,top_y_m=.04,bottom_y_m=0.)
    raw=data/'source.grp';raw.write_bytes(b'synthetic fixture geometry')
    meta['grp_sha256']=file_hash(raw);write_json(metadata,meta)
    source=data/'profile.json';write_json(source,{'vehicle':'fixture','provenance':{'client_version':'fixture','datamine_commit':'a'*40}})
    catalog=root/'catalog.json';write_json(catalog,{'vehicles':[{'id':'fixture','kind':'ground',
        'source_attributes':{'client':{'source_version':'fixture','geometry_sha256':file_hash(raw),'configuration_sha256':file_hash(source)}}}]})
    ammo=root/'ammunition.json';write_json(ammo,[profile()])
    def rel(p):return p.relative_to(root).as_posix()
    write_json(data/'jobs/fixture.json',{'vehicle_id':'fixture','status':'complete','profile_path':rel(source),
        'layer_metadata':rel(metadata),'raw_path':rel(raw),'profile_sha256':file_hash(source),'grp_sha256':file_hash(raw)})
    return catalog,ammo,metadata,raw

class RangeRebuildTests(unittest.TestCase):
    def test_incremental_profiles_preserve_verified_old_ranges_and_reject_changed_source(self):
        import sys
        sys.path.insert(0,str(ROOT/'scripts'))
        from prepare_range_profiles import prepare
        old=profile();old_range=source_range_profile(old,100)
        new=copy.deepcopy(old);new['name']='new_round';new['provenance']['penetration_source_ammo_ids']=['fixture::new']
        bundle,counts,rejections,preserved=prepare([old,new],[100,500],None,previous=[old],existing=[old,old_range])
        self.assertEqual(bundle[:2],[old,old_range]);self.assertEqual(preserved,2)
        self.assertEqual(counts['KE'],2);self.assertEqual(rejections,[])
        self.assertEqual([p['distance_m'] for p in bundle[3:]],[100,500])
        changed=copy.deepcopy(old);changed['penetration_mm']=101.
        with self.assertRaisesRegex(ValueError,'Previous source profiles changed'):
            prepare([changed,new],[100],None,previous=[old],existing=[old,old_range])
        with self.assertRaisesRegex(ValueError,'supplied together'):
            prepare([old],[100],None,previous=[old])
        with self.assertRaisesRegex(ValueError,'conflicts'):
            prepare([old,new],[100],None,previous=[old],existing=[old,new])

    def test_ce_range_requires_exact_source_scalar_and_preserves_unknown_flight(self):
        with tempfile.TemporaryDirectory() as temporary:
            p,source=ce_profile(temporary);original=copy.deepcopy(p)
            results=[source_range_profile(p,d,temporary) for d in (10,100,500,1000)]
            self.assertEqual(p,original)
            self.assertEqual([r['distance_m'] for r in results],[10,100,500,1000])
            for r in results:
                self.assertNotIn('impact_distance_m',r)
                self.assertEqual(r['penetration_mm'],100)
                self.assertEqual(r['provenance']['range_rebuild']['range_reference'],'conditional_at_impact')
            with self.assertRaisesRegex(ValueError,'captured source'):source_range_profile(p,100)
            changed=copy.deepcopy(p);changed['penetration_mm']=101
            with self.assertRaisesRegex(ValueError,'constant penetration'):source_range_profile(changed,100,temporary)
            changed=copy.deepcopy(p);changed['provenance']['bullet_name']='other'
            with self.assertRaisesRegex(ValueError,'absent'):source_range_profile(changed,100,temporary)
            with self.assertRaisesRegex(ValueError,'original reference'):source_range_profile(results[0],500,temporary)
            source.write_text(source.read_text().replace('100','101'))
            with self.assertRaisesRegex(ValueError,'hash mismatch'):source_range_profile(p,100,temporary)

    def test_only_native_source_points_and_exact_ownership_are_accepted(self):
        original=profile();before=copy.deepcopy(original);derived=source_range_profile(original,500)
        self.assertEqual(original,before)
        self.assertEqual(derived['distance_m'],500)
        self.assertEqual(derived['provenance']['range_rebuild']['impact_penetration_mm'],50)
        for distance in (50,2000,-1,True,float('nan')):
            with self.assertRaises(ValueError):source_range_profile(original,distance)
        original['provenance']['penetration_source_ammo_ids']=[]
        with self.assertRaises(ValueError):source_range_profile(original,100)

    def test_range_is_evaluated_at_first_armor_and_is_camera_translation_invariant(self):
        values=[]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for offset in (0,20):
                directory=root/str(offset);directory.mkdir()
                rows=[(0,0,offset+.7,offset+.71,1.,np.inf),(0,1,offset+2.,offset+2.1,1.,np.inf)]
                model=PreparedLayers(make_layers(directory,rows,[material('armor',80),material('protected')],1),CONFIG)
                near=model.calculate(source_range_profile(profile(),100))[0]
                far=model.calculate(source_range_profile(profile(),500))[0]
                self.assertGreater(near.item(),far.item());self.assertEqual(far.item(),0.)
                values.append((near,far))
            for a,b in zip(*values):np.testing.assert_array_equal(a,b)

    def test_atomic_generation_resume_and_corruption_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,ammo,metadata,raw=source_fixture(root)
            untouched={str(p):file_hash(p) for p in (catalog,ammo,metadata,metadata.with_suffix('.npz'),raw)}
            output=root/'new-ranges'
            first=rebuild(root,catalog,ammo,ROOT/'config/pipeline.json',output,distances=[100,500])
            self.assertEqual(first['computed_maps'],2);self.assertEqual(first['pending_maps'],0)
            self.assertEqual(first['source_npz_probability_maps_read'],0)
            resumed=rebuild(root,catalog,ammo,ROOT/'config/pipeline.json',output,distances=[100,500],resume=True)
            self.assertEqual(resumed['computed_maps'],0);self.assertEqual(resumed['reused_maps'],2)
            next((output/'arrays').glob('*.npz')).write_bytes(b'corrupt generated cache')
            repaired=rebuild(root,catalog,ammo,ROOT/'config/pipeline.json',output,distances=[100,500],resume=True)
            self.assertEqual(repaired['computed_maps'],1);self.assertEqual(repaired['reused_maps'],1)
            self.assertEqual(untouched,{name:file_hash(name) for name in untouched})

    def test_limit_bounds_new_work_so_resume_advances_past_completed_maps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,ammo,metadata,raw=source_fixture(root)
            output=root/'batched'
            first=rebuild(root,catalog,ammo,ROOT/'config/pipeline.json',output,distances=[100,500],limit=1,resume=True)
            self.assertEqual(first['computed_maps'],1);self.assertEqual(first['pending_maps'],1)
            second=rebuild(root,catalog,ammo,ROOT/'config/pipeline.json',output,distances=[100,500],limit=1,resume=True)
            self.assertEqual(second['computed_maps'],1);self.assertEqual(second['reused_maps'],1)
            self.assertEqual(second['pending_maps'],0)

    def test_missing_range_and_broken_independent_source_report_blockers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,ammo,metadata,raw=source_fixture(root)
            self.assertEqual(rebuild(root,catalog,ammo,ROOT/'config/pipeline.json',root/'missing',distances=[2000])['status'],'blocked')
            raw.write_bytes(b'changed raw source')
            result=rebuild(root,catalog,ammo,ROOT/'config/pipeline.json',root/'broken',distances=[100])
            self.assertEqual(result['status'],'blocked');self.assertTrue(result['binding_errors'])

    def test_legacy_profile_output_is_unchanged_when_no_range_requested(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);catalog,ammo,metadata,raw=source_fixture(root)
            model=PreparedLayers(metadata,CONFIG)
            legacy=model.calculate(profile())
            self.assertEqual(legacy[0].item(),1.)
            self.assertEqual(legacy[1].item(),80.)
            self.assertEqual(legacy[2].item(),3)

if __name__=='__main__':unittest.main()

