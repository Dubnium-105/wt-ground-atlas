import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_forecast import settings  # sets the backend import path
from wt_atlas.forecast.rule_snapshot import rating,reconcile,capture_snapshot,import_snapshot,SOURCE_PATHS,RANKS_PATH,WPCOST_PATH

SOURCE={'version':'fixture','commit':'a'*40,'wpcost_url':'https://example.test/pinned',
        'wpcost_sha256':'b'*64,'ranks_sha256':'c'*64}

class RuleSnapshotTests(unittest.TestCase):
    def catalog(self):
        return {'snapshot':{'game_version':None,'source_status':'unknown','client_source':{'version':'older'}},
                'vehicles':[{'id':'plane_i','nation':'usa','kind':'aircraft','br':{'current':6.7,'planned':6.7}}]}

    def test_tank_mode_field_wins_and_missing_never_becomes_reserve(self):
        self.assertEqual(rating({'economicRankTankHistorical':16,'economicRankHistorical':30}),(6.3,'economicRankTankHistorical'))
        self.assertEqual(rating({'economicRankHistorical':16}),(6.3,'economicRankHistorical'))
        self.assertEqual(rating({}),(None,None))
        for v in [True,-1,1.5,float('nan'),'16']:
            with self.subTest(v=v),self.assertRaises(ValueError):rating({'economicRankHistorical':v})

    def test_exact_case_only_binding_updates_br_but_not_physics_identity(self):
        c=self.catalog();before=copy.deepcopy(c)
        updated,report=reconcile(c,{'plane_I':{'economicRankTankHistorical':16,'country':'country_usa'}},SOURCE)
        self.assertEqual(c,before)
        self.assertEqual(updated['vehicles'][0]['br'],{'current':6.3,'planned':6.3})
        self.assertEqual(report['counts'],{'updated':1})
        self.assertEqual(updated['snapshot']['client_source'],before['snapshot']['client_source'])
        self.assertIsNone(updated['snapshot']['game_version'])
        self.assertEqual(updated['snapshot']['source_status'],'unknown')

    def test_collision_country_conflict_and_fuzzy_alias_are_not_accepted(self):
        unit={'economicRankHistorical':16,'country':'country_usa'}
        for data in [{'plane_I':unit,'plane_i':unit},{'plane_i':{**unit,'country':'country_germany'}}]:
            with self.assertRaises(ValueError):reconcile(self.catalog(),data,SOURCE)
        result,report=reconcile(self.catalog(),{'plane_1':unit},SOURCE)
        self.assertEqual(report['counts'],{'source_unit_missing':1})
        self.assertEqual(result['vehicles'][0]['br']['current'],6.7)

    def test_nonselectable_components_and_future_scenarios_are_preserved(self):
        c=self.catalog();c['vehicles'][0]['br']={'current':None,'planned':None}
        data={'plane_i':{'economicRankHistorical':16,'country':'country_usa'}}
        result,report=reconcile(c,data,SOURCE)
        self.assertIsNone(result['vehicles'][0]['br']['current'])
        self.assertEqual(report['counts'],{'catalog_nonselectable':1})
        c=self.catalog();c['vehicles'][0]['br']['planned']=7.
        result,_=reconcile(c,data,SOURCE)
        self.assertEqual(result['vehicles'][0]['br']['planned'],7.)

    def payloads(self):
        return {'version':b'2.59.0.94\n',RANKS_PATH:b'fixture rating source',
                WPCOST_PATH:json.dumps({'plane_i':{'economicRankTankHistorical':16,'country':'country_usa'}}).encode(),
                SOURCE_PATHS[-1]:b'{"respawn_points":{}}'}

    def responder(self,fail_path=None,bad_blob=False):
        values=self.payloads()
        def fetch(request,timeout):
            url=request.full_url
            path=next(p for p in SOURCE_PATHS if url.endswith('/'+p) or '/'+p+'?ref=' in url)
            if fail_path==path:raise OSError('fixture network failure')
            data=values[path]
            if url.startswith('https://api.github.com/'):
                data=json.dumps({'type':'file','size':len(data),'sha':'0'*40 if bad_blob else hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()}).encode()
            return io.BytesIO(data)
        return fetch

    def test_verified_capture_offline_replay_and_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'sources';commit='a'*40
            with patch('urllib.request.urlopen',side_effect=self.responder()) as network:
                receipt=capture_snapshot(root,commit)
                self.assertEqual(network.call_count,8)
            with patch('urllib.request.urlopen',side_effect=AssertionError('offline touched network')):
                self.assertEqual(capture_snapshot(root,commit,offline=True),receipt)
            catalog=Path(temp)/'catalog.json';catalog.write_text(json.dumps(self.catalog()),encoding='utf-8')
            source_hash=hashlib.sha256(self.payloads()[RANKS_PATH]).hexdigest()
            with patch('wt_atlas.forecast.rule_snapshot.RANKS_SCRIPT_SHA256',source_hash):
                result,report=import_snapshot(catalog,root,commit)
            self.assertEqual(result['vehicles'][0]['br']['current'],6.3)
            self.assertFalse(report['promoted_release_status'])
            with self.assertRaises(ValueError):capture_snapshot(root,'b'*40,offline=True)
            (root/'version').write_text('2.59.0.95',encoding='utf-8')
            with self.assertRaises(ValueError):capture_snapshot(root,commit,offline=True)

    def test_partial_network_and_wrong_git_blob_never_publish_a_receipt(self):
        for kwargs in [{'fail_path':WPCOST_PATH},{'bad_blob':True}]:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                with patch('urllib.request.urlopen',side_effect=self.responder(**kwargs)),self.assertRaises((OSError,ValueError)):
                    capture_snapshot(root,'a'*40)
                self.assertFalse((root/'capture.json').exists())
                with self.assertRaises(ValueError):capture_snapshot(root,'a'*40,offline=True)
                with patch('urllib.request.urlopen',side_effect=self.responder()) as network:
                    capture_snapshot(root,'a'*40)
                    self.assertEqual(network.call_count,7 if 'fail_path' in kwargs else 8)

if __name__=='__main__':unittest.main()
