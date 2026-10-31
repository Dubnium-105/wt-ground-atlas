import copy
import unittest
from types import SimpleNamespace

from test_forecast import settings
from wt_atlas.forecast.engine import enumerate_variants


class PrimaryAmmunitionTests(unittest.TestCase):
    def fixture(self):
        proof={'primary_modification_id':'upgrade','required_modification_ids':['upgrade'],
               'unit_path':'unit.blk','weapon_path':'rocket.blk'}
        ammo={'id':'vehicle::rocket','native_ammunition_binding':copy.deepcopy(proof)}
        profile={'name':'rocket-profile','distance_m':100.,'provenance':{'recovery_bindings':{ammo['id']:proof}}}
        vehicle={'id':'vehicle','kind':'ground','nation':'usa','br':{'current':9.},'ammo':[ammo]}
        catalog={'snapshot':{'game_version':'test'},'vehicles':[vehicle]}
        atlas=SimpleNamespace(bindings=lambda v:[(ammo,profile)])
        return catalog,atlas,ammo,profile

    def test_unlocked_modification_is_required_and_identity_retains_it(self):
        catalog,atlas,ammo,profile=self.fixture();config=settings()
        config['ownership']={'require_unlocked':True,'unlocked_modifications_by_vehicle':{'vehicle':[]}}
        variants,rejected=enumerate_variants(catalog,atlas,config)
        self.assertEqual(variants,[]);self.assertTrue(any(r['reason']=='modifications_unavailable' for r in rejected))
        config['ownership']['unlocked_modifications_by_vehicle']['vehicle']=['upgrade']
        variants,_=enumerate_variants(catalog,atlas,config)
        self.assertEqual(len(variants),1);self.assertEqual(variants[0]['primary_modification_id'],'upgrade')
        ammo['native_ammunition_binding']['required_modification_ids']=[]
        variants,rejected=enumerate_variants(catalog,atlas,config)
        self.assertEqual(variants,[]);self.assertTrue(any(r['reason']=='primary_ammunition_binding_mismatch' for r in rejected))

    def test_dynamic_rating_must_allow_the_primary_configuration(self):
        catalog,atlas,ammo,profile=self.fixture();config=settings()
        catalog['vehicles'][0]['rating_configurations']={'ground_rb':{'status':'official_verified',
            'source':{'url':'https://example.test'},'options':[{'id':'one','br':{'current':9.},
            'allowed_primary_armament_ids':['other']}]}}
        self.assertEqual(enumerate_variants(catalog,atlas,config)[0],[])
        catalog['vehicles'][0]['rating_configurations']['ground_rb']['options'][0]['allowed_primary_armament_ids']=['upgrade']
        self.assertEqual(len(enumerate_variants(catalog,atlas,config)[0]),1)

    def test_missing_catalog_receipt_cannot_bypass_profile_requirement(self):
        catalog,atlas,ammo,profile=self.fixture();ammo.pop('native_ammunition_binding')
        variants,rejected=enumerate_variants(catalog,atlas,settings())
        self.assertEqual(variants,[]);self.assertTrue(any(r['reason']=='primary_ammunition_binding_mismatch' for r in rejected))


if __name__=='__main__':unittest.main()
