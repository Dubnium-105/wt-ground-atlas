import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.client_payloads import CapturedPayloads,import_payloads
from wt_atlas.common import write_json,file_hash


SOURCE={'version':'fixture','commit':'a'*40,'wpcost_url':'https://example.test/pinned',
        'wpcost_sha256':'b'*64,'client_capture_sha256':'c'*64,'client_source':'client://fixture'}


def blk(data,indent=''):
    lines=[]
    for key,value in data.items():
        for v in value if isinstance(value,list) else [value]:
            if isinstance(v,dict):lines.extend([indent+key+' {',blk(v,indent+'  '),indent+'}'])
            elif isinstance(v,bool):lines.append(indent+key+':b = '+str(v).lower())
            elif isinstance(v,int):lines.append(indent+key+':i = '+str(v))
            elif isinstance(v,float):lines.append(indent+key+':r = '+str(v))
            else:lines.append(indent+key+':t = "'+v+'"')
    return '\n'.join(lines)+'\n'


class ClientPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.model={'weapon_presets':{'preset':{'name':'plane_payload','blk':'gamedata/flightmodels/weaponpresets/payload.blk'}}}
        self.selection={'Weapon':{'blk':'gamedata/weapons/container.blk','trigger':'aam'}}
        self.put('gamedata/flightmodels/plane.blk',self.model)
        self.put('gamedata/flightmodels/weaponpresets/payload.blk',self.selection)
        self.put('gamedata/weapons/container.blk',{'container':True,'blk':'gamedata/weapons/rocket.blk','bullets':3})
        self.put('gamedata/weapons/rocket.blk',{'bullets':1,'rocket':{'bulletName':'fixture_missile','guidanceType':'radar','rangeMax':1000.}})
        self.vehicle={'id':'plane','kind':'aircraft','missing':[],'loadouts':[{'id':'plane_payload','legal':False,'weapons':[{'id':'stale'}]}]}
        self.unit={'weapons':{'plane_payload':{}}}

    def tearDown(self):self.temp.cleanup()
    def put(self,path,data):
        target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(blk(data),encoding='utf-8')
    def adapter(self):return CapturedPayloads(self.root,'fixture','a'*40,['aam','bombs'])

    def test_nested_payload_is_rebuilt_from_sources_without_mission_or_sp_estimates(self):
        before=copy.deepcopy(self.vehicle)
        result,report=self.adapter().verify(self.vehicle,self.unit,SOURCE)
        self.assertEqual(self.vehicle,before);self.assertEqual(report['verified'],1)
        loadout=result['loadouts'][0];weapon=loadout['weapons'][0]
        self.assertEqual(weapon['id'],'fixture_missile');self.assertEqual(weapon['count'],3)
        self.assertEqual(len(result['client_payload_sources']),4)
        self.assertEqual(len(weapon['provenance']['source_sha256']),64)
        self.assertEqual(loadout['source_version'],'fixture')
        self.assertIsNone(loadout['spawn_cost']);self.assertNotIn('hit_probability',weapon)

    def test_missing_shop_identity_never_promotes_previous_payload(self):
        result,report=self.adapter().verify(self.vehicle,{'weapons':{}},SOURCE)
        self.assertEqual(report['verified'],0);self.assertEqual(report['quarantined'],1)
        self.assertFalse(result['loadouts'][0]['legal'])

    def test_unsupported_source_relation_stays_an_explicit_rejection(self):
        unit=copy.deepcopy(self.unit)
        unit['weapons']['custom_presets']={'slot':{'index':1,'selected':{
            'DependentWeaponPreset':{'absent':2},'rocketguns_fixture':1}}}
        result,report=self.adapter().verify(self.vehicle,unit,SOURCE)
        self.assertEqual(report['verified'],0)
        self.assertIn('unsupported_source_constraints',report['records'][0]['errors'])

    def test_unsupported_carried_item_cannot_be_silently_omitted(self):
        self.selection['Weapon']=[self.selection['Weapon'],{'blk':'gamedata/weapons/unknown.blk','trigger':'unknown'}]
        self.put('gamedata/flightmodels/weaponpresets/payload.blk',self.selection)
        result,report=self.adapter().verify(self.vehicle,self.unit,SOURCE)
        self.assertEqual(report['verified'],0)
        self.assertIn('unsupported_carried_weapon_trigger',report['records'][0]['errors'])

    def test_cached_source_mutation_is_detected_and_unsafe_paths_rejected(self):
        adapter=self.adapter();adapter.verify(self.vehicle,self.unit,SOURCE)
        self.put('gamedata/weapons/rocket.blk',{'bullets':2,'rocket':{'bulletName':'changed'}})
        with self.assertRaisesRegex(ValueError,'changed during import'):adapter.verify(self.vehicle,self.unit,SOURCE)
        with self.assertRaises(ValueError):adapter.read('../outside.blk')

    def test_container_cycle_is_quarantined(self):
        self.put('gamedata/weapons/container.blk',{'container':True,'blk':'gamedata/weapons/container.blk','bullets':1})
        result,report=self.adapter().verify(self.vehicle,self.unit,SOURCE)
        self.assertEqual(report['verified'],0)
        self.assertIn('cyclic',report['parser_errors'][0]['error'])

    def test_container_explicit_count_applies_to_single_projectile_reference(self):
        self.put('gamedata/weapons/rocket.blk',{'rocket':{'bulletName':'fixture_missile'}})
        result,report=self.adapter().verify(self.vehicle,self.unit,SOURCE)
        self.assertEqual(report['verified'],1)
        self.assertEqual(result['loadouts'][0]['weapons'][0]['count'],3)
        self.selection['Weapon']['blk']='gamedata/weapons/rocket.blk'
        self.put('gamedata/flightmodels/weaponpresets/payload.blk',self.selection)
        result,report=self.adapter().verify(self.vehicle,self.unit,SOURCE)
        self.assertEqual(report['verified'],0)
        self.assertEqual(report['parser_errors'][0]['error'],'projectile count unknown')

    def test_inline_projectile_launcher_retains_explicit_quantity(self):
        self.put('gamedata/weapons/container.blk',{'container':True,'bullets':2,'rocket':{'bulletName':'fixture_missile'}})
        result,report=self.adapter().verify(self.vehicle,self.unit,SOURCE)
        self.assertEqual(report['verified'],1)
        self.assertEqual(result['loadouts'][0]['weapons'][0]['count'],2)

    def test_import_requires_matching_packs_rules_and_exact_shop_content(self):
        from wt_atlas.forecast.rule_snapshot import WPCOST_PATH
        rules=self.root/'rules';rules.mkdir();(rules/'version').write_text('fixture')
        write_json(rules/WPCOST_PATH,{'plane':self.unit})
        self.put('char/config/wpcost.blk',{'plane':self.unit})
        packs={}
        for name,extracted in [('aces.vromfs.bin',self.root),('char.vromfs.bin',self.root/'char')]:
            raw=self.root/name;raw.write_bytes(b'synthetic capture '+name.encode())
            packs[name]={'version':'fixture','saved':str(raw),'sha256':file_hash(raw),'extracted':str(extracted)}
        capture=self.root/'capture.json';write_json(capture,{'packs':packs})
        catalog=self.root/'catalog.json';v=copy.deepcopy(self.vehicle)
        v['rule_snapshot_reference']={'binding':{'source_id':'plane'}}
        write_json(catalog,{'vehicles':[v]})
        config=self.root/'config.json';write_json(config,{'datamine':{'external_weapon_triggers':['aam']}})
        with patch('wt_atlas.forecast.rule_snapshot.verify_capture',return_value={}):
            result,report=import_payloads(catalog,capture,rules,'a'*40,config)
            self.assertEqual(report['counts']['verified_loadouts'],1)
            self.assertNotIn('source_status',result['snapshot'])
            self.put('char/config/wpcost.blk',{'plane':{'weapons':{}}})
            with self.assertRaisesRegex(ValueError,'differ from'):
                import_payloads(catalog,capture,rules,'a'*40,config)
            (rules/'version').write_text('different')
            with self.assertRaisesRegex(ValueError,'version mismatch'):
                import_payloads(catalog,capture,rules,'a'*40,config)


if __name__=='__main__':unittest.main()
