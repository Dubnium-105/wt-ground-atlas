import copy
import csv
import unittest

import test_forecast_nested_missile as nested_fixtures
from wt_atlas.ammunition_recovery import Localization,MountedAmmunition
from wt_atlas.ammunition_selectors import REVIEWED_SCRIPTS
from wt_atlas.dual_use_missile_jet import ce_profile,jet_source,source_evidence
from wt_atlas.range_rebuild import source_range_profile


class DualUseMissileTests(unittest.TestCase):
    def setUp(self):
        self.fixture=nested_fixtures.NestedMissileTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        f=self.fixture;f.bullet['rocket']['bulletType']='sam_tank';f.bullet['rocket']['cumulativeDamage']['armorPower']=900.
        f.ammo['penetration']=[{'angle_deg':0.,'distance_m':None,'mm':900.}]
        f.tables[('Missile launcher','Missile')]['points']=f.ammo['penetration']
        f.weapon['bullet']=f.bullet;f.put(f.weapon_path,f.weapon)
        with (f.lang/'units_weaponry.csv').open('a',encoding='utf8',newline='') as stream:
            csv.writer(stream,delimiter=';').writerow(['round','Missile'])
    def binding(self,selection=False):
        f=self.fixture
        if selection:
            f.unit['modifications']={'choice':{}};f.put(f.unit_path,f.unit)
            f.weapon['choice']={'bullet':f.bullet};f.put(f.weapon_path,f.weapon)
            rules={'selectors':{'choice':{'kind':'additiveBulletMod','selector':'choice'}},
                   'evidence':{'source_version':'test','scripts':{k:{'lf_sha256':v} for k,v in REVIEWED_SCRIPTS.items()}}}
        else:rules=None
        adapter=MountedAmmunition(f.root,Localization(f.lang,include_defaults=True),default_rules=f.rules,
                                  selector_rules=rules,structural_projectiles=True)
        return adapter.bind(adapter.candidates({'id':'vehicle'}),f.ammo)
    def profile(self,selection=False):
        f=self.fixture;return ce_profile(f.ammo,self.binding(selection),f.tables,f.wiki,f.response,'test','a'*40)

    def test_dual_use_default_and_declared_selector_recheck_sources(self):
        for selected in (False,True):
            p=self.profile(selected);self.assertEqual(p['penetration_mm'],900.)
            self.assertEqual(source_evidence(p,self.fixture.root)['chemical_energy']['jet_type'],'sam_tank')
            ranged=source_range_profile(p,100,self.fixture.root)
            self.assertEqual(ranged['provenance']['range_rebuild']['source_evidence']['dual_use_missile_adapter'],'dual-use-missile-jet-v1')
            self.assertFalse(p['provenance']['chemical_energy']['flight_and_activation_modeled'])

    def test_plain_sam_tandem_and_unverified_charges_are_excluded(self):
        b=self.fixture.bullet
        for mutate in (lambda x:x.update(bulletType='sam_tank'),
                       lambda x:x['rocket'].update(bulletType='atgm_tank'),
                       lambda x:x['rocket'].pop('cumulativeDamage'),
                       lambda x:x['rocket'].update(tandemPrecharge={}),
                       lambda x:x['rocket'].update(cumulativeDeviationAngle=25.)):
            changed=copy.deepcopy(b);mutate(changed)
            with self.subTest(changed=changed),self.assertRaises(ValueError):jet_source(changed)

    def test_inconsistent_wiki_source_or_receipt_is_rejected(self):
        f=self.fixture;p=self.profile(True)
        changed=copy.deepcopy(p);proof=changed['provenance']['recovery_bindings'][f.ammo['id']];proof['selector_rules']['scripts']={}
        with self.assertRaisesRegex(ValueError,'selector rules'):source_evidence(changed,f.root)
        changed=copy.deepcopy(p);changed['provenance']['recovery_bindings'][f.ammo['id']]['localization'][0]['value']='Other'
        with self.assertRaisesRegex(ValueError,'localization'):source_evidence(changed,f.root)
        f.ammo['penetration'][0]['mm']=800.
        with self.assertRaisesRegex(ValueError,'penetration_mismatch'):
            ce_profile(f.ammo,self.binding(True),f.tables,f.wiki,f.response,'test','a'*40)

    def test_dual_use_maps_are_computed_revalidated_and_resumed(self):
        from test_forecast_range_rebuild import source_fixture
        from wt_atlas.common import read_json,write_json,file_hash
        from wt_atlas.native_rebuild import rebuild
        from wt_atlas.forecast.inventory import Inventory
        f=self.fixture;repo=f.root/'geometry-source';catalog_path,ammo_path,layers,raw=source_fixture(repo)
        p=self.profile(True);p['provenance']['client_version']='fixture'
        p['provenance']['recovery_bindings'][f.ammo['id']]['selector_rules']['source_version']='fixture'
        ranged=[source_range_profile(p,d,f.root) for d in (100,1000)]
        write_json(ammo_path,[p,*ranged]);catalog=read_json(catalog_path);catalog['vehicles'][0]['nation']='fixture'
        catalog['vehicles'].append({'id':'vehicle','kind':'ground','ammo':[f.ammo],'nation':'fixture',
                                    'source_attributes':{'client':{'source_version':'fixture'}}})
        write_json(catalog_path,catalog);request=f.root/'requests.json'
        write_json(request,{'catalog_sha256':file_hash(catalog_path),'ammunition_sha256':file_hash(ammo_path),
            'candidate_rebuild_requests':[{'target_vehicle_id':'fixture','profile_id':q['name'],'attacker_ids':['vehicle'],
                'required_yaws_deg':read_json(layers)['plan']['angles_deg']} for q in ranged]})
        out=f.root/'maps';first=rebuild(repo,catalog_path,ammo_path,request,out,source_root=f.root)
        self.assertEqual(first['computed_maps'],2);self.assertIn('dual_use_missile_profile_code_sha256',first['model'])
        self.assertEqual(rebuild(repo,catalog_path,ammo_path,request,out,source_root=f.root,resume=True)['reused_maps'],2)
        index=Inventory(f.root/'inventory.sqlite')
        try:
            index.sync(out,ammo_path,catalog);self.assertEqual(index.verify(out)['verified_arrays'],2)
        finally:index.close()
