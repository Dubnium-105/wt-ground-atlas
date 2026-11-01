import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from test_pipeline import CONFIG, make_layers, material
from wt_atlas.cpu_model import PreparedLayers
from wt_atlas.research_maps import save_map
from wt_atlas.forecast.exported_bullet_points import exact_export_points, research_profiles


class ExportedBulletPointsTests(unittest.TestCase):
    def fixture(self):
        projectile={'bulletName':'round','bulletType':'ap_t','mass':1.,'speed':900.,'caliber':.03,'Cx':.25,
            'damage':{'kinetic':{'damageType':'generic'}},'slopeEffectPreset':'ap','ricochetPreset':'ap'}
        distances=[10,100,500,1000]; values=[{'0':v,'30':v*.8} for v in [80.,78.,65.,50.]]
        entry={'bulletTypeId':'ap_t','units':['test_unit'],'raw':{'mass':1.,'speed':900.,'caliber':.03,'Cx':.25},
            'penetrationByUnit':{'test_unit':{'kinetic':{'distances':distances,'values':values}}},
            'ttxByUnit':{'test_unit':{'armorPiercingDist':distances,'armorPiercingKinetic':values}}}
        preset={'row':{'caliberToArmor':1.,'slopeEffect0':[0.,1.],'slopeEffect90':[90.,1.],
                       'ricochetProbability0':[0.,0.],'ricochetProbability90':[90.,1.]}}
        return {'bullets':{'round':entry}}, projectile, {'slope':{'ap':preset},'ricochet':{'ap':preset},'dispersion':.05}

    def test_produces_only_source_exact_reference_points(self):
        db,bullet,response=self.fixture(); profiles=research_profiles(db,'test_unit',bullet,response,{'source_version':'fixture','datamine_commit':'fixture'})
        self.assertEqual([p['penetration_mm'] for p in profiles],[80.,78.,65.,50.])
        self.assertTrue(all(p['reference_only'] and not p['provenance']['export_version_verified'] for p in profiles))
        self.assertTrue(all(p['normalized_distance_curve']==[] for p in profiles))

    def test_exact_points_drive_the_existing_layer_model(self):
        db,bullet,response=self.fixture();profiles=research_profiles(db,'test_unit',bullet,response,{'source_version':'fixture','datamine_commit':'fixture'})
        with tempfile.TemporaryDirectory() as temp:
            rows=[(0,0,.7,.71,1.,np.inf),(0,1,2.,2.1,1.,np.inf)]
            model=PreparedLayers(make_layers(Path(temp),rows,[material('armor',60),material('protected')],1),CONFIG)
            near=model.calculate(profiles[0])[0].item();far=model.calculate(profiles[-1])[0].item()
            self.assertGreater(near,far)
            actual=tuple(a.reshape((1,1,1)) for a in model.calculate(profiles[0]))
            plan={'angles_deg':[17],'pixel_pitch_m':.04,'world_width_m':.04,'top_y_m':.04,'bottom_y_m':0.}
            identity={'target_id':'fixture','target_sha256':'fixture'}
            saved,reused=save_map(Path(temp)/'maps',identity,profiles[0],actual,plan)
            self.assertTrue(saved['reference_only']);self.assertFalse(reused)
            self.assertTrue(save_map(Path(temp)/'maps',identity,profiles[0],actual,plan)[1])

    def test_rejects_ambiguous_or_mismatched_points(self):
        db,b,_=self.fixture();bad=copy.deepcopy(db);bad['bullets']['round']['units'].append('other')
        with self.assertRaisesRegex(ValueError,'single-unit'):exact_export_points(bad,'test_unit',b)
        bad=copy.deepcopy(db);bad['bullets']['round']['raw']['speed']=901
        with self.assertRaisesRegex(ValueError,'mismatch'):exact_export_points(bad,'test_unit',b)
        bad=copy.deepcopy(db);ttx=bad['bullets']['round']['ttxByUnit']['test_unit'];ttx['armorPiercing']=ttx.pop('armorPiercingKinetic')
        with self.assertRaisesRegex(ValueError,'disagree'):exact_export_points(bad,'test_unit',b)
        bad=copy.deepcopy(db);del bad['bullets']['round']['penetrationByUnit']['test_unit']['kinetic']['values'][0]['0']
        with self.assertRaises(ValueError):exact_export_points(bad,'test_unit',b)
        bad=copy.deepcopy(db);bad['bullets']['round']['penetrationByUnit']['test_unit']['kinetic']['distances'][0]=20
        with self.assertRaises(ValueError):exact_export_points(bad,'test_unit',b)

    def test_cli_imports_a_source_bound_synthetic_export_and_protects_source_files(self):
        script=Path(__file__).resolve().parents[1]/'scripts/import_exported_bullet_points.py'
        spec=importlib.util.spec_from_file_location('test_export_import_cli',script);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        db,_,_=self.fixture()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);aces=root/'aces';gui=root/'gui';aces_pack=root/'aces.pack';gui_pack=root/'gui.pack'
            aces_pack.write_bytes(b'fixture aces');gui_pack.write_bytes(b'fixture gui')
            def put(base,name,text):
                p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf8');return p
            script_path=put(gui,cli.EXPORTER,'fixture reviewed exporter\n')
            unit=put(aces,'gamedata/units/tankmodels/test_unit.blk',
                'commonWeapons {\n Weapon {\n trigger:t = "gunner0"\n blk:t = "gamedata/weapons/fixture.blk"\n }\n}\nmodifications {\n chosen {\n }\n}\n')
            put(aces,'gamedata/weapons/fixture.blk',
                'chosen {\n bullet {\n bulletName:t = "round"\n bulletType:t = "ap_t"\n mass:r = 1\n speed:r = 900\n caliber:r = 0.03\n Cx:r = 0.25\n slopeEffectPreset:t = "ap"\n ricochetPreset:t = "ap"\n damage {\n kinetic {\n damageType:t = "generic"\n }\n }\n }\n}\n')
            put(aces,'gamedata/damage_model/slope_effect.blk','ap {\n row {\n caliberToArmor:r = 1\n slopeEffect0:p2 = 0, 1\n slopeEffect90:p2 = 90, 1\n }\n}\n')
            put(aces,'gamedata/damage_model/ricochet.blk','ap {\n row {\n caliberToArmor:r = 1\n ricochetProbability0:p2 = 0, 0\n ricochetProbability90:p2 = 90, 1\n }\n}\n')
            put(aces,'gamedata/damage_model/damage_system.blk','pierceDispersion:r = 0.05\n')
            sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            capture=root/'capture.json';capture.write_text(json.dumps({'packs':{
                'aces.vromfs.bin':{'version':cli.VERSION,'sha256':sha(aces_pack),'saved':str(aces_pack),'extracted':str(aces)},
                'gui.vromfs.bin':{'version':cli.VERSION,'sha256':sha(gui_pack),'saved':str(gui_pack),'extracted':str(gui)}}}),encoding='utf8')
            database=root/'weapons_db.json';database.write_text(json.dumps(db),encoding='utf8');out=root/'result.json'
            args=[str(script),'--capture',str(capture),'--database',str(database),'--out',str(out),
                  '--unit','test_unit','--weapon','gamedata/weapons/fixture.blk','--selector','chosen','--projectile','round']
            normalized=hashlib.sha256(script_path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
            with patch.object(cli,'ACES',sha(aces_pack)),patch.object(cli,'GUI',sha(gui_pack)),patch.object(cli,'EXPORTER_LF',normalized),patch.object(sys,'argv',args):
                cli.main()
                result=json.loads(out.read_text(encoding='utf8'))
                self.assertEqual(len(result['profiles']),4);self.assertFalse(result['publishable'])
                self.assertFalse(result['export_version_verified'])
                original=unit.read_bytes();args[args.index('--out')+1]=str(unit)
                with self.assertRaisesRegex(ValueError,'overlaps captured source'):cli.main()
                self.assertEqual(unit.read_bytes(),original)


if __name__ == '__main__':unittest.main()
