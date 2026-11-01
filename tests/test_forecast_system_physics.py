import unittest
from pathlib import Path
import json
import numpy as np
from wt_atlas.forecast.data import digest
from wt_atlas.forecast.system_physics import declared_members,unknown_exposure_bounds,evaluate


class SystemPhysicsTests(unittest.TestCase):
    def test_unknown_member_selection_preserves_extrema_without_inventing_a_mean(self):
        x=unknown_exposure_bounds([{'lower':.1,'upper':.3},{'lower':.5,'upper':.9}])
        self.assertEqual(x,{'lower':.1,'upper':.9,'mean':None,'target_selection_weights':'unknown'})
        self.assertEqual(unknown_exposure_bounds([{'lower':.1,'upper':.3},{'lower':0.,'upper':1.}])['upper'],1.)
        with self.assertRaises(ValueError):unknown_exposure_bounds([{'lower':.9,'upper':.1}])
        a=[{'skill':-1.,'weight':.5,'lower':0.,'upper':0.},{'skill':1.,'weight':.5,'lower':1.,'upper':1.}]
        b=[{'skill':-1.,'weight':.5,'lower':1.,'upper':1.},{'skill':1.,'weight':.5,'lower':0.,'upper':0.}]
        result=unknown_exposure_bounds([{'lower':.5,'upper':.5,'by_skill':a},{'lower':.5,'upper':.5,'by_skill':b}])
        self.assertEqual((result['lower'],result['upper']),(0.,1.))
        self.assertTrue(result['selection_may_depend_on_skill'])

    def test_requires_complete_consistent_system_membership(self):
        members=[{'vehicle_id':'base','role':'basic','count':1},{'vehicle_id':'part','role':'component','count':2}]
        system={'basic_vehicle_id':'base','members':members,'provenance':{'method':'official-Wiki-multiunit-membership','url':'wiki'}}
        base={'id':'base','kind':'ground','system_id':'base','system_role':'basic','nation':'x','wiki_url':'wiki','multi_vehicle_system':system}
        part={'id':'part','kind':'ground','system_id':'base','system_role':'component','nation':'x','multi_vehicle_system':{'members':members}}
        catalog={'vehicles':[base,part]};self.assertEqual([v['vehicle_id'] for v in declared_members(catalog,'base')],['base','part'])
        part['multi_vehicle_system']['members']=members[:1]
        with self.assertRaisesRegex(ValueError,'Inconsistent'):declared_members(catalog,'base')

    def test_evaluates_real_aim_path_and_preserves_missing_member_uncertainty(self):
        config=json.loads((Path(__file__).resolve().parents[1]/'config/forecast.full-ground-rb.json').read_text(encoding='utf8'))
        profile={'name':'p','mode':'KE','distance_m':10.,'provenance':{'client_version':'fixture','datamine_commit':'fixture'}}
        members=[{'vehicle_id':'base','role':'basic','count':1},{'vehicle_id':'part','role':'component','count':2}]
        class FakeAtlas:
            def __init__(self):
                self.missing=False;self.meta={name:{'target_sha256':name,'ammo_profile_sha256':digest(profile),
                    'source_version':'fixture','datamine_commit':'fixture','array_sha256':'hash-'+name} for name in ('base','part')}
            def map_for(self,target,ammo,distance):return None if self.missing and target=='part' else target
            def bindings(self,attacker):return [({'id':'ammo'},profile)]
            def load(self,key):return {'angles_deg':np.array([0.]),'penetration_probability':np.array([[[.2 if key=='base' else .8]]],dtype=np.float32),
                'status':np.array([[[3]]],dtype=np.uint8),'pixel_pitch_m':np.float64(.04)}
        atlas=FakeAtlas();bounds={name:{'target_sha256':name} for name in ('base','part')};scenario=config['scenarios'][0]
        result=evaluate(atlas,members,bounds,{'firepower':{},'sensors':{}},{'id':'ammo'},[profile],scenario,config,'normal')
        self.assertLess(result['members'][0]['lower'],result['members'][1]['lower'])
        self.assertLessEqual(result['exposure_bounds']['lower'],result['members'][0]['lower'])
        self.assertGreaterEqual(result['exposure_bounds']['upper'],result['members'][1]['upper'])
        atlas.missing=True;result=evaluate(atlas,members,bounds,{'firepower':{},'sensors':{}},{'id':'ammo'},[profile],scenario,config,'normal')
        self.assertEqual((result['exposure_bounds']['lower'],result['exposure_bounds']['upper']),(0.,1.))
        atlas.missing=False;bounds['part']['target_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'source mismatch'):
            evaluate(atlas,members,bounds,{'firepower':{},'sensors':{}},{'id':'ammo'},[profile],scenario,config,'normal')
        with self.assertRaisesRegex(ValueError,'not exactly owned'):
            evaluate(atlas,members,bounds,{'firepower':{},'sensors':{}},{'id':'different'},[profile],scenario,config,'normal')


if __name__=='__main__':unittest.main()
