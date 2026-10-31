import copy
import csv
from pathlib import Path
import tempfile
import unittest

from test_forecast_ammunition_recovery import blk
from wt_atlas.ammunition_recovery import Localization, MountedAmmunition
from wt_atlas.common import file_hash
from wt_atlas.default_belts import REVIEWED_SCRIPTS
from wt_atlas.native_binding.ordered_blk import as_json, parse_ordered_blk
from wt_atlas.nested_missile_jet import ce_profile, source_evidence
from wt_atlas.range_rebuild import source_range_profile


class NestedMissileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.lang=self.root/'lang';self.lang.mkdir()
        for name,rows in [('menu_options.csv',[]),('units_modifications.csv',[]),
                          ('units_weaponry.csv',[('weapons/launcher','Missile launcher'),('weapons/round/short','Missile')])]:
            with (self.lang/name).open('w',encoding='utf-8-sig',newline='') as f:
                w=csv.writer(f,delimiter=';');w.writerow(['<ID|readonly|noverify>','<English>']);w.writerows(rows)
        self.unit_path='gamedata/units/tankmodels/vehicle.blk';self.weapon_path='gamedata/weapons/launcher.blk'
        self.unit={'commonWeapons':{'Weapon':{'blk':self.weapon_path,'trigger':'gunner0'}}}
        self.bullet={'bulletName':'round','bulletType':'atgm_tank','caliber':.125,'rocket':{
            'bulletType':'atgm_tank','caliber':.135,'ricochetPreset':'atgm','minDistance':30.,
            'cumulativeDamage':{'armorPower':500.}}}
        self.weapon={'isBulletBelt':False,'useSingleIconForBullet':True,'notUseDefaultBulletInGui':True,'bullet':self.bullet}
        self.put(self.unit_path,self.unit);self.put(self.weapon_path,self.weapon)
        ricochet={'atgm':{'table':{'caliberToArmor':1.,'ricochetProbability0':(0.,1.),'ricochetProbability90':(90.,0.)}}}
        self.put('gamedata/damage_model/ricochet.blk',ricochet);self.put('gamedata/damage_model/damage_system.blk',{'pierceDispersion':.05})
        self.response={'ricochet':as_json(parse_ordered_blk(self.root/'gamedata/damage_model/ricochet.blk')),'dispersion':.05,
            'hashes':{p:file_hash(self.root/p) for p in ['gamedata/damage_model/ricochet.blk','gamedata/damage_model/damage_system.blk']}}
        self.rules={'source_version':'test','scripts':{k:{'lf_sha256':v} for k,v in REVIEWED_SCRIPTS.items()}}
        self.ammo={'id':'vehicle::Missile','name':'Missile','caliber_mm':135.,'provenance':{'parent_weapon_name':'Missile launcher'},
                   'penetration':[{'angle_deg':0.,'distance_m':None,'mm':500.}]}
        self.tables={('Missile launcher','Missile'):{'points':self.ammo['penetration'],'maximum_only':False}}
        self.wiki={'path':'unit_vehicle.html.gz','sha256':'a'*64,'url':'https://wiki.warthunder.com/unit/vehicle'}
    def tearDown(self):self.temp.cleanup()
    def put(self,path,value):
        dest=self.root/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(blk(value),encoding='utf8')
    def adapter(self):
        return MountedAmmunition(self.root,Localization(self.lang,include_defaults=True),default_rules=self.rules,
                                 structural_projectiles=True,nested_missiles=True)
    def binding(self):
        adapter=self.adapter();return adapter.bind(adapter.candidates({'id':'vehicle'}),self.ammo)
    def profile(self):return ce_profile(self.ammo,self.binding(),self.tables,self.wiki,self.response,'test','a'*40)

    def test_exact_mount_and_nested_caliber_produce_replayable_profile(self):
        p=self.profile();self.assertEqual(p['damage_caliber_mm'],135.);self.assertEqual(p['penetration_mm'],500.)
        self.assertEqual(p['provenance']['recovery_bindings'][self.ammo['id']]['catalog_caliber_parameter'],'rocket.caliber')
        self.assertEqual(source_evidence(p,self.root)['penetration_mm'],500.)
        self.assertEqual(source_range_profile(p,100,self.root)['provenance']['range_rebuild']['source_evidence']['nested_missile_adapter'],'nested-missile-jet-v1')
        self.assertFalse(p['provenance']['chemical_energy']['flight_and_activation_modeled'])

    def test_wrong_label_mount_or_projectile_cannot_bind(self):
        for field,value in [('name','missile'),('provenance',{'parent_weapon_name':'Other'})]:
            changed={**self.ammo,field:value};adapter=self.adapter()
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'selection_missing'):
                adapter.bind(adapter.candidates({'id':'vehicle'}),changed)
        for field,value in [('isBulletBelt',True),('useSingleIconForBullet',False),('notUseDefaultBulletInGui',False)]:
            self.put(self.weapon_path,{**self.weapon,field:value});adapter=self.adapter()
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'selection_missing'):
                adapter.bind(adapter.candidates({'id':'vehicle'}),self.ammo)
        self.put(self.weapon_path,self.weapon);unit=copy.deepcopy(self.unit);unit['commonWeapons']['Weapon']['dummy']=True;self.put(self.unit_path,unit)
        adapter=self.adapter()
        with self.assertRaisesRegex(ValueError,'selection_missing'):adapter.bind(adapter.candidates({'id':'vehicle'}),self.ammo)

    def test_tampered_receipts_and_caliber_are_rejected(self):
        p=self.profile()
        for key,value in [('catalog_caliber_mm',125.),('nested_missile_label',{'parent_weapon_name':'Other','ammo_name':'Missile'}),
                          ('nested_missile_rules',{'source_version':'test','scripts':{}})]:
            changed=copy.deepcopy(p);changed['provenance']['recovery_bindings'][self.ammo['id']][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):source_evidence(changed,self.root)
        changed={**self.ammo,'caliber_mm':125.}
        with self.assertRaisesRegex(ValueError,'nested_rocket_caliber_mismatch'):
            ce_profile(changed,self.binding(),self.tables,self.wiki,self.response,'test','a'*40)

    def test_native_generation_revalidates_and_resumes_nested_profiles(self):
        from test_forecast_range_rebuild import source_fixture
        from wt_atlas.common import read_json,write_json
        from wt_atlas.native_rebuild import rebuild
        from wt_atlas.forecast.inventory import Inventory
        repo=self.root/'geometry-source';catalog_path,ammo_path,layers,raw=source_fixture(repo)
        p=self.profile();p['provenance']['client_version']='fixture'
        p['provenance']['recovery_bindings'][self.ammo['id']]['nested_missile_rules']['source_version']='fixture'
        ranged=[source_range_profile(p,d,self.root) for d in (100,1000)]
        write_json(ammo_path,[p,*ranged]);catalog=read_json(catalog_path);catalog['vehicles'][0]['nation']='fixture'
        catalog['vehicles'].append({'id':'vehicle','kind':'ground','ammo':[self.ammo],'nation':'fixture',
                                    'source_attributes':{'client':{'source_version':'fixture'}}})
        write_json(catalog_path,catalog);request=self.root/'requests.json'
        write_json(request,{'catalog_sha256':file_hash(catalog_path),'ammunition_sha256':file_hash(ammo_path),
            'candidate_rebuild_requests':[{'target_vehicle_id':'fixture','profile_id':q['name'],'attacker_ids':['vehicle'],
                'required_yaws_deg':read_json(layers)['plan']['angles_deg']} for q in ranged]})
        out=self.root/'maps';first=rebuild(repo,catalog_path,ammo_path,request,out,source_root=self.root)
        self.assertEqual(first['computed_maps'],2);self.assertIn('nested_missile_profile_code_sha256',first['model'])
        self.assertEqual(rebuild(repo,catalog_path,ammo_path,request,out,source_root=self.root,resume=True)['reused_maps'],2)
        index=Inventory(self.root/'inventory.sqlite')
        try:
            index.sync(out,ammo_path,catalog);self.assertEqual(index.verify(out)['verified_arrays'],2)
        finally:index.close()
