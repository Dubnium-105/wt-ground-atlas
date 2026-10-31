"""Synthetic acceptance for explicit empty presets and inherited fixed armament."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.client_payloads import CapturedPayloads,import_payloads
from wt_atlas.common import read_json,write_json,file_hash
from wt_atlas.forecast.capabilities import loadout_errors
from wt_atlas.forecast.mount_rules import check_mounts
from wt_atlas.forecast.engine import enumerate_variants
from wt_atlas.primary_armament import default_declarations,verified_rules
from test_forecast_client_payloads import blk,SOURCE


class PrimaryArmamentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.config=read_json(ROOT/'config/sources.json')
        self.weapon={'blk':'gun.blk','trigger':'machine gun','bullets':100,'emitter':'left'}
        self.model={'weapon_presets':{'preset':{'name':'p','blk':'preset.blk'}},
                    'commonWeapons':{'Weapon':[self.weapon,{**self.weapon,'emitter':'right'}]}}
        self.vehicle={'id':'plane','nation':'a','kind':'aircraft','br':{'current':3.0},'missing':[],'loadouts':[]}
        self.unit={'weapons':{'p':{}}}
        self.put('gun.blk',{'bullets':500,'mass':23.,'bullet':{'bulletType':'ap'}})
        self.put('preset.blk',{})
    def tearDown(self):self.temp.cleanup()
    def put(self,path,data):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(blk(data),encoding='utf-8')
    def verify(self):
        self.put('gamedata/flightmodels/plane.blk',self.model)
        a=CapturedPayloads(self.root,'fixture','a'*40,self.config['datamine']['external_weapon_triggers'])
        return a.verify(self.vehicle,self.unit,SOURCE)

    def test_empty_declared_preset_retains_exact_fixed_guns_and_ammunition(self):
        v,r=self.verify();self.assertEqual(r['verified'],1)
        l=v['loadouts'][0];self.assertEqual(l['weapons'],[]);self.assertEqual(l['mounts'],[])
        self.assertEqual(l['external_store_state'],'source_declared_empty')
        self.assertEqual(l['primary_armament_id'],'')
        self.assertEqual(sum(w['count'] for w in l['fixed_weapons']),2)
        self.assertEqual(sum(w['ammunition_count'] for w in l['fixed_weapons']),200)
        self.assertEqual(v['default_primary_armament']['weapons'][0]['provenance']['source_sha256'],file_hash(self.root/'gun.blk'))
        self.assertEqual(loadout_errors(l,v,{}),[])
        variants,rejected=enumerate_variants({'vehicles':[v]},object())
        self.assertEqual(len(variants),1);self.assertTrue(variants[0]['selection_ready']);self.assertEqual(rejected,[])

    def test_missing_file_is_never_inferred_as_an_empty_preset(self):
        self.model['weapon_presets']['preset']['blk']='missing.blk'
        v,r=self.verify();self.assertEqual(r['verified'],0)
        self.assertEqual(len(r['parser_errors']),1);self.assertEqual(v['loadouts'],[])

    def test_missing_shop_record_does_not_promote_an_empty_file(self):
        self.unit={'weapons':{}}
        v,r=self.verify();self.assertEqual(r['verified'],0)
        self.assertIn('source_shop_preset_missing',r['records'][0]['errors'])

    def test_unknown_empty_preset_directives_are_rejected(self):
        self.put('preset.blk',{'futureWeaponReplacement':{'id':'unknown'}})
        _,r=self.verify();self.assertEqual(r['verified'],0)
        self.assertEqual(r['parser_errors'][0]['error'],'Unsupported empty preset directives')

    def test_known_cosmetic_directives_preserve_empty_store_identity(self):
        self.put('preset.blk',{'hideNodes':{'node':'pylon'},'smokeScreenCount':2})
        v,r=self.verify();self.assertEqual(r['verified'],1)
        self.assertEqual(v['loadouts'][0]['external_store_state'],'source_declared_empty')

    def test_custom_mounts_require_source_proof_for_an_empty_selection(self):
        self.unit['weapons']['custom_presets']={'slot':{'index':1,'gun':{'gun':1}}}
        v,r=self.verify();self.assertEqual(r['verified'],1)
        l=v['loadouts'][0];self.assertEqual(loadout_errors(l,v,{}),[])
        self.assertEqual(check_mounts([],v['mount_rule'])['errors'],['mount_selection_missing'])
        for field in ('external_store_state','mount_rule_audit','provenance'):
            forged=copy.deepcopy(l);forged.pop(field)
            self.assertIn('mount_selection_missing',loadout_errors(forged,v,{}))

    def test_changed_primary_identity_or_summary_blocks_candidate(self):
        v,_=self.verify();l=v['loadouts'][0]
        changed=copy.deepcopy(v);changed['default_primary_armament']['weapons'][0]['ammunition_count']=1
        self.assertIn('primary_armament_identity_mismatch',loadout_errors(l,changed,{}))
        forged=copy.deepcopy(l);forged['fixed_weapons']=[]
        self.assertIn('primary_armament_identity_mismatch',loadout_errors(forged,v,{}))
        self.assertIn('forbidden_by_rating_configuration',loadout_errors(l,v,{'allowed_weapon_ids':[]}))
        self.assertEqual(loadout_errors(l,{**v,'max_payload_kg':0},{}),[], 'fixed guns are not external payload mass')

    def test_primary_modification_is_listed_but_not_silently_selected(self):
        self.model['modifications']={'upgrade':{'effects':{'commonWeapons':{'Weapon':{**self.weapon,'bullets':999}}}}}
        v,_=self.verify();p=v['default_primary_armament']
        self.assertEqual(p['available_other_primary_modifications'],['upgrade'])
        self.assertEqual(sum(w['ammunition_count'] for w in p['weapons']),200)

    def test_fixed_slot_resolution_and_required_modification(self):
        self.model['commonWeapons']={'Weapon':{'slot':0,'preset':'internal'}}
        self.model['WeaponSlots']={'WeaponSlot':{'index':0,'WeaponPreset':{
            'name':'internal','reqModification':'gun-upgrade','Weapon':self.weapon}}}
        v,r=self.verify();self.assertEqual(r['verified'],1)
        l=v['loadouts'][0];self.assertEqual(len(l['fixed_weapons']),1)
        self.assertIn('gun-upgrade',l['required_modification_ids'])
        self.assertIn('modifications_unavailable',loadout_errors(l,v,{},unlocked=[],require_unlocked=True))
        self.model['commonWeapons']['Weapon']['preset']='absent'
        with self.assertRaisesRegex(ValueError,'primary mount preset missing'):self.verify()

    def test_legacy_pylons_deduplicate_exact_declarations_and_skip_dummies(self):
        self.model['commonWeapons']['Weapon'].append(copy.deepcopy(self.weapon))
        self.model['commonWeapons']['Weapon'].append({'dummy':True,'trigger':'countermeasures'})
        self.model['WeaponPilons']={'WeaponSlot':[{'WeaponPreset':{'Weapon':copy.deepcopy(self.weapon)}},
            {'WeaponPreset':{'Weapon':{**self.weapon,'emitter':'extra'}}}]}
        declarations,ignored=default_declarations(self.model)
        self.assertEqual(len(declarations),3);self.assertEqual(len(ignored),1)
        v,r=self.verify();self.assertEqual(r['verified'],1)
        self.assertEqual(sum(w['count'] for w in v['loadouts'][0]['fixed_weapons']),3)

    def test_missing_fixed_weapon_cannot_be_treated_as_an_unarmed_aircraft(self):
        self.model['commonWeapons']['Weapon'][0]['blk']='missing-gun.blk'
        with self.assertRaisesRegex(ValueError,'Missing/outside input'):self.verify()

    def test_gui_rules_require_version_pack_and_reviewed_script_content(self):
        raw=b'fixture\n';self.root.joinpath('pack').write_bytes(b'pack')
        self.root.joinpath('script.nut').write_bytes(b'fixture\r\n')
        pack={'version':'fixture','saved':str(self.root/'pack'),'sha256':file_hash(self.root/'pack'),'extracted':str(self.root)}
        capture={'packs':{'gui.vromfs.bin':pack}}
        with patch('wt_atlas.primary_armament.REVIEWED_SCRIPTS',{'script.nut':hashlib.sha256(raw).hexdigest()}):
            self.assertEqual(verified_rules(capture,'fixture')['source_version'],'fixture')
            with self.assertRaisesRegex(ValueError,'capture mismatch'):verified_rules(capture,'other')
            self.root.joinpath('script.nut').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'Unreviewed'):verified_rules(capture,'fixture')

    def test_failed_vehicle_refresh_quarantines_previously_legal_payload(self):
        from wt_atlas.forecast.rule_snapshot import WPCOST_PATH
        rules=self.root/'rules';rules.mkdir();(rules/'version').write_text('fixture')
        write_json(rules/WPCOST_PATH,{'plane':self.unit});self.put('char/config/wpcost.blk',{'plane':self.unit})
        packs={}
        for name,path in [('aces.vromfs.bin',self.root),('char.vromfs.bin',self.root/'char')]:
            p=self.root/name;p.write_bytes(b'synthetic '+name.encode());packs[name]={'version':'fixture','saved':str(p),'sha256':file_hash(p),'extracted':str(path)}
        write_json(self.root/'capture.json',{'packs':packs});write_json(self.root/'config.json',self.config)
        self.vehicle['loadouts']=[{'id':'old','legal':True,'source_status':'official_verified','weapons':[]}]
        write_json(self.root/'catalog.json',{'vehicles':[self.vehicle]})
        with patch('wt_atlas.forecast.rule_snapshot.verify_capture',return_value={}),patch('wt_atlas.client_payloads.verify_primary_rules',return_value={'scope':'synthetic'}):
            c,r=import_payloads(self.root/'catalog.json',self.root/'capture.json',rules,'a'*40,self.root/'config.json')
        self.assertEqual(r['counts']['vehicle_errors'],1)
        self.assertFalse(c['vehicles'][0]['loadouts'][0]['legal'])
        self.assertEqual(c['vehicles'][0]['loadouts'][0]['refresh_blocker'],'captured_client_refresh_failed')


if __name__=='__main__':unittest.main()
