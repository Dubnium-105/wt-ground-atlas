import copy
from pathlib import Path
import tempfile
import unittest

from test_forecast_ammunition_recovery import blk
from wt_atlas.ammunition_recovery import consolidate
from wt_atlas.chemical_projectiles import ce_profile, jet_source, source_evidence
from wt_atlas.common import file_hash
from wt_atlas.native_binding.ordered_blk import as_json, parse_ordered_blk
from wt_atlas.range_rebuild import source_range_profile


class ChemicalProjectileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.bullet={'bulletName':'test_heat','bulletType':'heat_grenade_tank','caliber':.08,
            'ricochetPreset':'outer','rocket':{'bulletType':'heat_tank','caliber':.08,
            'ricochetPreset':'inner','minDistance':30.,'maxDistance':6000.,
            'cumulativeDamage':{'armorPower':330.}}}
        self.unit_path='gamedata/units/tankmodels/vehicle.blk';self.weapon_path='gamedata/weapons/weapon.blk'
        self.put(self.unit_path,{'commonWeapons':{'Weapon':{'blk':self.weapon_path}}})
        self.put(self.weapon_path,{'belt':{'bullet':self.bullet}})
        self.response={'ricochet':{'inner':{'table':{'caliberToArmor':1.,'ricochetProbability0':(0.,1.),'ricochetProbability90':(90.,0.)}}},'dispersion':.05,'hashes':{}}
        for name,data in [('ricochet',self.response['ricochet']),('damage_system',{'pierceDispersion':.05})]:
            path='gamedata/damage_model/'+name+'.blk';self.put(path,data);self.response['hashes'][path]=file_hash(self.root/path)
        self.response['ricochet']=as_json(parse_ordered_blk(self.root/'gamedata/damage_model/ricochet.blk'))
        self.ammo={'id':'vehicle::test','name':'Test','caliber_mm':80.,'provenance':{'parent_weapon_name':'Test gun'},
            'penetration':[{'angle_deg':0.,'distance_m':10.,'mm':330.}]}
        self.tables={('Test gun','Test'):{'points':self.ammo['penetration'],'maximum_only':False}}
        self.wiki={'path':'unit_vehicle.html.gz','sha256':'a'*64,'url':'https://wiki.warthunder.com/unit/vehicle'}
    def tearDown(self):self.temp.cleanup()
    def put(self,path,data):
        dest=self.root/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(blk(data),encoding='utf-8')
    def binding(self):
        return {'unit_path':self.unit_path,'unit_sha256':file_hash(self.root/self.unit_path),
            'weapon_path':self.weapon_path,'weapon_sha256':file_hash(self.root/self.weapon_path),
            'selector':'belt','bullets':[self.bullet],'mounts':[{'trigger':'gunner0'}],
            'localization':[{'key':'belt/name','value':'Test'}]}
    def profile(self):
        return ce_profile(self.ammo,self.binding(),self.tables,self.wiki,self.response,'test','a'*40)

    def test_nested_jet_uses_its_own_response_and_preserves_source(self):
        before=copy.deepcopy(self.bullet);profile=self.profile()
        self.assertEqual(profile['penetration_mm'],330.)
        self.assertEqual(profile['provenance']['chemical_energy']['ricochet_preset'],'inner')
        self.assertEqual(profile['provenance']['chemical_energy']['source_min_distance_m'],30.)
        self.assertEqual(profile['provenance']['chemical_energy']['jet_path'],'rocket')
        self.assertEqual(self.bullet,before)
        for distance in (10,100,500,1000):
            ranged=source_range_profile(profile,distance,self.root)
            self.assertEqual(ranged['distance_m'],distance)
            self.assertEqual(ranged['penetration_mm'],330.)
            self.assertEqual(ranged['provenance']['range_rebuild']['range_reference'],'conditional_at_impact')
            self.assertFalse(ranged['provenance']['chemical_energy']['flight_and_activation_modeled'])

    def test_he_or_uses_exact_projectile_and_explicit_damage_caliber(self):
        self.bullet={'bulletName':'test_he_or','bulletType':'he_or_tank','caliber':.08,'damageCaliber':.05,
                     'ricochetPreset':'inner','cumulativeDamage':{'armorPower':300.}}
        self.put(self.weapon_path,{'belt':{'bullet':self.bullet}})
        profile=self.profile();self.assertEqual(profile['damage_caliber_mm'],50.)
        self.assertEqual(profile['provenance']['bullet_name'],'test_he_or')
        self.assertEqual(len(profile['provenance']['recovery_bindings'][self.ammo['id']]['wiki_normal_penetration_mismatches']),1)
        self.assertEqual(source_evidence(profile,self.root)['penetration_mm'],300.)

    def test_hesh_tandem_efp_and_directed_jets_are_not_single_jet_profiles(self):
        for typ in ('hesh_tank','atgm_tandem_tank','sam_tank'):
            changed=copy.deepcopy(self.bullet);changed['bulletType']=typ
            with self.subTest(typ=typ),self.assertRaisesRegex(ValueError,'type_unsupported'):jet_source(changed)
        for extra in ({'cumulativeDeviationAngle':30.},{'cumulativeByNormal':True},{'tandemPrecharge':{}},
                      {'cumulativeDamage':{'armorPower':330.,'damageType':'explosiveFormedProjectile'}}):
            changed=copy.deepcopy(self.bullet);changed['rocket'].update(extra)
            with self.subTest(extra=extra),self.assertRaisesRegex(ValueError,'directed_charge'):jet_source(changed)

    def test_single_jet_atgm_has_conditional_endpoint_only(self):
        self.bullet['bulletType']='atgm_tank';self.bullet['rocket']['bulletType']='atgm_tank'
        self.put(self.weapon_path,{'belt':{'bullet':self.bullet}})
        p=self.profile();self.assertFalse(p['provenance']['chemical_energy']['flight_and_activation_modeled'])
        self.assertEqual(source_evidence(p,self.root)['penetration_mm'],330.)

    def test_chemical_and_kinetic_wiki_tables_are_not_combined(self):
        self.ammo['penetration']=[{'angle_deg':0.,'distance_m':None,'mm':330.}]
        self.tables[('Test gun','Test')]['points']=self.ammo['penetration']+[{'angle_deg':0.,'distance_m':10.,'mm':5.}]
        p=self.profile();proof=p['provenance']['recovery_bindings'][self.ammo['id']]
        self.assertEqual(p['penetration_mm'],330.)
        self.assertEqual(proof['wiki_comparison_scope'],'constant_chemical_table')
        self.assertEqual(proof['wiki_additional_distance_points'][0]['mm'],5.)
        self.assertEqual(proof['wiki_normal_penetration_mismatches'],[])
        self.ammo['penetration'][0]['mm']=200.
        # The independently parsed table must not be silently rewritten.
        self.tables[('Test gun','Test')]['points'][0]={'angle_deg':0.,'distance_m':None,'mm':330.}
        with self.assertRaisesRegex(ValueError,'exact_wiki_table'):self.profile()

    def test_tampered_source_and_profile_cannot_be_derived(self):
        p=self.profile()
        for field,value in [('penetration_mm',400.),('damage_caliber_mm',90.),('ricochet_rows',[]),('air_loss_mm_per_m',0.)]:
            changed=copy.deepcopy(p);changed[field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'physical profile'):
                source_range_profile(changed,100,self.root)
        path=self.root/self.weapon_path;path.write_text(path.read_text()+'\n// changed source\n')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):source_range_profile(p,100,self.root)

    def test_primary_modification_jet_rechecks_mount_and_prerequisites(self):
        from wt_atlas.primary_armament import REVIEWED_SCRIPTS
        self.put(self.unit_path,{'commonWeapons':{'Weapon':{'blk':'other.blk'}},'modifications':{
            'upgrade':{'effects':{'commonWeapons':{'Weapon':{'blk':self.weapon_path,'trigger':'gunner0'}}}}}})
        binding=self.binding();binding.update(primary_modification_id='upgrade',required_modification_ids=['upgrade'],
                                              primary_armament_rules={'source_version':'test','scripts':{k:{'lf_sha256':v} for k,v in REVIEWED_SCRIPTS.items()}})
        profile=ce_profile(self.ammo,binding,self.tables,self.wiki,self.response,'test','a'*40)
        self.assertEqual(source_evidence(profile,self.root)['penetration_mm'],330.)
        for key,value in [('primary_modification_id','missing'),('required_modification_ids',[]),('mounts',[{'trigger':'wrong'}])]:
            changed=copy.deepcopy(profile);changed['provenance']['recovery_bindings'][self.ammo['id']][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):source_evidence(changed,self.root)
        changed=copy.deepcopy(profile);del changed['provenance']['recovery_bindings'][self.ammo['id']]['primary_modification_id']
        with self.assertRaisesRegex(ValueError,'not mounted'):source_evidence(changed,self.root)

    def test_root_rocket_rechecks_raw_block_and_reviewed_gui_rules(self):
        from wt_atlas.default_belts import REVIEWED_SCRIPTS
        self.bullet={'bulletName':'rocket','bulletType':'atgm_tank','caliber':.08,
                     'ricochetPreset':'inner','cumulativeDamage':{'armorPower':330.}}
        self.put(self.weapon_path,{'rocketGun':True,'rocket':self.bullet})
        binding=self.binding();binding.update(selector='@default',projectile_block='root_rocket',
            root_rocket_rules={'source_version':'test','scripts':{k:{'lf_sha256':v} for k,v in REVIEWED_SCRIPTS.items()}})
        profile=ce_profile(self.ammo,binding,self.tables,self.wiki,self.response,'test','a'*40)
        self.assertEqual(source_evidence(profile,self.root)['penetration_mm'],330.)
        changed=copy.deepcopy(profile);changed['provenance']['recovery_bindings'][self.ammo['id']]['root_rocket_rules']['scripts']={}
        with self.assertRaisesRegex(ValueError,'localization rules'):source_evidence(changed,self.root)
        changed=copy.deepcopy(profile);changed['provenance']['recovery_bindings'][self.ammo['id']]['projectile_block']='bullet'
        with self.assertRaisesRegex(ValueError,'Unknown projectile block'):source_evidence(changed,self.root)

    def test_mixed_source_cannot_use_a_maximum_or_one_matching_member(self):
        binding=self.binding();binding['bullets'].append({**self.bullet,'bulletName':'other'})
        with self.assertRaisesRegex(ValueError,'mixed_selection'):
            ce_profile(self.ammo,binding,self.tables,self.wiki,self.response,'test','a'*40)

    def test_consolidation_retains_each_mounted_source_and_rechecks_it(self):
        first=self.profile();self.unit_path='gamedata/units/tankmodels/second.blk'
        self.put(self.unit_path,{'commonWeapons':{'Weapon':{'blk':self.weapon_path}}})
        self.ammo['id']='second::test';second=self.profile()
        merged,_=consolidate([first,second]);self.assertEqual(len(merged),1)
        self.assertEqual(len(merged[0]['provenance']['recovery_bindings']),2)
        source_evidence(merged[0],self.root)
        self.put(self.unit_path,{'commonWeapons':{'Weapon':{'blk':'other'}}})
        with self.assertRaisesRegex(ValueError,'hash mismatch'):source_evidence(merged[0],self.root)

    def test_forged_receipt_cannot_change_projectile_or_vehicle(self):
        p=self.profile();proof=p['provenance']['recovery_bindings'][self.ammo['id']]
        proof['projectile_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'raw projectile'):source_evidence(p,self.root)
        p=self.profile();p['provenance']['penetration_source_ammo_ids']=['other::test']
        p['provenance']['recovery_bindings']={'other::test':p['provenance']['recovery_bindings'][self.ammo['id']]}
        with self.assertRaisesRegex(ValueError,'vehicle ownership'):source_evidence(p,self.root)

    def test_recovered_jet_computes_native_arrays_and_resumes_without_source_changes(self):
        from test_forecast_range_rebuild import source_fixture
        from wt_atlas.common import read_json, write_json
        from wt_atlas.native_rebuild import rebuild
        from wt_atlas.forecast.inventory import Inventory
        repo=self.root/'geometry-source';cat,ammo,layers,raw=source_fixture(repo)
        p=self.profile();p['provenance']['client_version']='fixture'
        ranged=[source_range_profile(p,d,self.root) for d in (100,1000)]
        write_json(ammo,[p,*ranged]);catalog=read_json(cat)
        catalog['vehicles'][0]['nation']='fixture'
        catalog['vehicles'].append({'id':'vehicle','kind':'ground','ammo':[self.ammo],
            'nation':'fixture',
            'source_attributes':{'client':{'source_version':'fixture'}}})
        write_json(cat,catalog)
        request=self.root/'requests.json';write_json(request,{'catalog_sha256':file_hash(cat),
            'ammunition_sha256':file_hash(ammo),'candidate_rebuild_requests':[
                {'target_vehicle_id':'fixture','profile_id':q['name'],'attacker_ids':['vehicle'],
                 'required_yaws_deg':read_json(layers)['plan']['angles_deg']} for q in ranged]})
        preserved={str(path):file_hash(path) for path in (cat,ammo,layers,layers.with_suffix('.npz'),raw,self.root/self.weapon_path)}
        out=self.root/'generated';first=rebuild(repo,cat,ammo,request,out,source_root=self.root)
        self.assertEqual(first['computed_maps'],2)
        self.assertEqual(first['source_probability_maps_read'],0)
        second=rebuild(repo,cat,ammo,request,out,source_root=self.root,resume=True)
        self.assertEqual(second['computed_maps'],0);self.assertEqual(second['reused_maps'],2)
        index=Inventory(self.root/'arrays.sqlite')
        try:
            index.sync(out,ammo,catalog)
            self.assertEqual(index.verify(out)['verified_arrays'],2)
        finally:index.close()
        self.assertEqual(preserved,{path:file_hash(path) for path in preserved})
