import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_forecast import ROOT
from wt_atlas.forecast.data import canonical, read_json
from wt_atlas.wiki_refresh import capture_wiki, refresh_catalog


class WikiRefreshTests(unittest.TestCase):
    def setUp(self):
        self.config=read_json(ROOT/'config/sources.json')
        self.rows={
            'ground':[['ground-id','Ground fixture','a',1,{'rb':25},0,None,[['medium_tank']]]],
            'aviation':[['air-id','Air fixture','a',1,{'rb':40,'trb':28},0,None,[['fighter']]]],
            'helicopters':[['heli-id','Helicopter fixture','a',1,{'rb':25},0,None,[['helicopter']]]],
        }

    def response(self,request,**kwargs):
        slug=request.full_url.rsplit('/',1)[-1]
        payload=canonical(self.rows[slug])
        result=io.BytesIO(("<html><script>window.WT_UnitList = '"+payload+"';</script></html>").encode())
        result.url=request.full_url
        return result

    def before(self,vehicles):
        cat={'snapshot':{'id':'old','game_version':'fixture-v1','client_source':{'version':'fixture-v1'},'source_status':'released'},
             'forecast_scenario':{'status':'proposed'},'vehicles':copy.deepcopy(vehicles)}
        for vehicle in cat['vehicles']:
            vehicle['br']={'current':8.0,'planned':10.0}
            vehicle.update(ammo=[{'id':vehicle['id']+'::round'}],analysis_target_sha256='a'*64,
                           source_attributes={'client':{'source_version':'fixture-v1'}},
                           configuration_id='physical-configuration')
        return cat

    def test_exact_id_refresh_uses_ground_rb_and_preserves_physical_sources(self):
        with tempfile.TemporaryDirectory() as temporary, patch('wt_atlas.wiki_refresh.urlopen',side_effect=self.response):
            manifest,vehicles=capture_wiki(self.config,Path(temporary))
            before=self.before(vehicles);snapshot=canonical(before)
            before['vehicles'][1]['loadouts']=[{'id':'stale-preset','legal':True,'weapons':[]}]
            before['vehicles'][0]['spawn_rule']={'status':'official_verified','source_version':'fixture-v1'}
            before['vehicles'][0]['rating_configurations']={'ground_rb':{'status':'official_verified','options':[]}}
            snapshot=canonical(before)
            current,receipt=refresh_catalog(before,manifest,vehicles)
            self.assertEqual(canonical(before),snapshot)
            by_id={v['id']:v for v in current['vehicles']}
            self.assertEqual(by_id['air-id']['br'],{'current':10.3,'planned':10.3})
            self.assertEqual(by_id['air-id']['br_source_mode'],'trb')
            self.assertEqual(by_id['ground-id']['ammo'],before['vehicles'][0]['ammo'])
            self.assertEqual(by_id['ground-id']['configuration_id'],'physical-configuration')
            self.assertEqual(by_id['ground-id']['analysis_target_sha256'],'a'*64)
            self.assertFalse(by_id['air-id']['loadouts'][0]['legal'])
            self.assertNotIn('spawn_rule',by_id['ground-id'])
            self.assertEqual(by_id['ground-id']['rating_configurations']['ground_rb']['status'],'unvalidated')
            self.assertIsNone(current['snapshot']['game_version'])
            self.assertEqual(current['snapshot']['source_status'],'unknown')
            self.assertNotIn('forecast_scenario',current)
            self.assertFalse(receipt['future_snapshot_created'])
            self.assertEqual(current['snapshot']['coverage']['catalog_vehicles'],3)
            self.assertEqual(current['snapshot']['coverage']['details_parsed'],0)
            self.assertEqual(current['snapshot']['coverage']['with_legal_loadout'],0)
            self.assertEqual(current['snapshot']['coverage']['quarantined_presets'],1)
            self.assertEqual(current['nations'],[{'id':'a','label':'a'}])
            self.assertTrue(all(r.get('reuse_physical_maps') for r in receipt['changes']['why_rebuilt']))

    def test_offline_replay_is_identical_and_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch('wt_atlas.wiki_refresh.urlopen',side_effect=self.response):
                manifest,vehicles=capture_wiki(self.config,root)
            with patch('wt_atlas.wiki_refresh.urlopen',side_effect=AssertionError('unexpected network')):
                self.assertEqual(capture_wiki(self.config,root,offline=True),(manifest,vehicles))
            (root/manifest['sources'][0]['path']).write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'checksum'):
                capture_wiki(self.config,root,offline=True)

    def test_partial_network_capture_keeps_previous_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch('wt_atlas.wiki_refresh.urlopen',side_effect=self.response):capture_wiki(self.config,root)
            before=(root/'latest.json').read_bytes()
            def failing(request,**kwargs):
                if request.full_url.endswith('/aviation'):raise OSError('unavailable')
                return self.response(request,**kwargs)
            with patch('wt_atlas.wiki_refresh.urlopen',side_effect=failing),self.assertRaises(OSError):
                capture_wiki(self.config,root)
            self.assertEqual((root/'latest.json').read_bytes(),before)

    def test_added_unobserved_and_conflicting_id_do_not_borrow_evidence(self):
        with tempfile.TemporaryDirectory() as temporary,patch('wt_atlas.wiki_refresh.urlopen',side_effect=self.response):
            manifest,vehicles=capture_wiki(self.config,Path(temporary));before=self.before(vehicles[:-1])
            missing=copy.deepcopy(before['vehicles'][0]);missing['id']='unobserved';before['vehicles'].append(missing)
            current,receipt=refresh_catalog(before,manifest,vehicles)
            self.assertEqual(receipt['added_ids'],['heli-id'])
            self.assertEqual(receipt['unobserved_ids'],['unobserved'])
            new=next(v for v in current['vehicles'] if v['id']=='heli-id')
            self.assertEqual(new['ammo'],[]);self.assertNotIn('analysis_target_sha256',new)
            absent=next(v for v in current['vehicles'] if v['id']=='unobserved')
            self.assertIsNone(absent['br']['current'])
            conflict=copy.deepcopy(vehicles);conflict[0]['nation']='b'
            with self.assertRaisesRegex(ValueError,'conflict'):refresh_catalog(before,manifest,conflict)

    def test_cli_outputs_separate_catalog_and_dry_run_preserves_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cache=root/'cache'
            with patch('wt_atlas.wiki_refresh.urlopen',side_effect=self.response):
                _,vehicles=capture_wiki(self.config,cache)
            before=root/'before.json';before.write_text(canonical(self.before(vehicles)),encoding='utf-8')
            output=root/'current.json';report=root/'report.json'
            command=[sys.executable,str(ROOT/'scripts/update_forecast.py'),'--refresh-wiki','--offline',
                     '--before',str(before),'--catalog-output',str(output),'--cache',str(cache),'--out',str(report)]
            run=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            saved=output.read_bytes();original=before.read_bytes()
            self.assertTrue(read_json(report)['catalog_written'])
            run=subprocess.run([*command,'--dry-run'],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(output.read_bytes(),saved);self.assertEqual(before.read_bytes(),original)
            self.assertFalse(read_json(report)['catalog_written'])
            invalid=command.copy();invalid[invalid.index('--catalog-output')+1]=str(before)
            self.assertNotEqual(subprocess.run(invalid,capture_output=True).returncode,0)
            self.assertEqual(before.read_bytes(),original)


if __name__=='__main__':unittest.main()
