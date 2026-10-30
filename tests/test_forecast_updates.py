import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from test_forecast import settings
from wt_atlas.forecast.updates import acquire,audit_aliases
from wt_atlas.forecast.statistics import calibrate,grouped_split


class UpdateTests(unittest.TestCase):
    def test_pinned_offline_source_and_cached_fallback(self):
        with tempfile.TemporaryDirectory() as t:
            r=Path(t);(r/'capture').write_bytes(b'official capture fixture')
            sha=hashlib.sha256((r/'capture').read_bytes()).hexdigest()
            m={'id':'test','game_version':'fixture','source_status':'unknown','sources':[{'kind':'wiki','path':'capture','sha256':sha,'game_version':'fixture'}]}
            first=acquire(m,r,r/'cache',offline=True);self.assertTrue(first['ready'])
            (r/'capture').unlink();second=acquire(m,r,r/'cache',offline=True)
            self.assertEqual(second['sources'][0]['status'],'cached_verified')
            (r/'cache'/sha).write_bytes(b'corrupt');third=acquire(m,r,r/'cache',offline=True)
            self.assertFalse(third['ready'])

    def test_network_failure_and_unpinned_commit_report_blockers(self):
        with tempfile.TemporaryDirectory() as t:
            m={'id':'test','game_version':'fixture','sources':[{'kind':'wiki','url':'https://wiki.warthunder.com/ground','sha256':'0'*64,'game_version':'fixture'}]}
            with patch('wt_atlas.forecast.updates.urlopen',side_effect=OSError('offline')):
                r=acquire(m,Path(t),Path(t)/'cache',retries=0)
            self.assertFalse(r['ready']);self.assertEqual(r['sources'][0]['error'],'offline')
            m['sources'][0].update(kind='datamine',url='https://raw.githubusercontent.com/gszabi99/War-Thunder-Datamine/master/file')
            self.assertIn('pinned',acquire(m,Path(t),Path(t)/'cache')['sources'][0]['error'])

    def test_ambiguous_alias_not_silently_bound(self):
        r=audit_aliases({'vehicles':[{'id':'a'},{'id':'b'}]},[{'alias':'same','vehicle_id':'a','source':'fixture'},{'alias':'same','vehicle_id':'b','source':'fixture'}])
        self.assertFalse(r['accepted']);self.assertEqual(len(r['conflicts']),1)


class StatisticsTests(unittest.TestCase):
    def packet(self):
        rng=np.random.default_rng(42)
        rows=[{'observation_id':str(i),'player_id':str(i//4),'match_id':str(i//4),'timestamp':f'2026-10-{1+i//8:02d}T{i%8:02d}:00:00Z','stratum':'novice' if i%2 else 'normal','game_version':'fixture','prediction':.8,'outcome':int(rng.random()<.5)} for i in range(160)]
        return {'source':'synthetic','sampling_window':'synthetic','game_version':'fixture','mode':'ground_rb','sample_count':160,'deduplication':'observation_id','bias_notes':'synthetic only','authorization':'synthetic fixture','endpoint':'penetration','observations':rows}

    def test_player_match_and_time_disjoint_calibration(self):
        packet=self.packet();split=grouped_split(packet['observations'])
        for a,b in [('train','validation'),('train','test'),('validation','test')]:
            for field in ('player_id','match_id'):self.assertFalse({r[field] for r in split[a]} & {r[field] for r in split[b]})
        self.assertLess(max(r['timestamp'] for r in split['train']),min(r['timestamp'] for r in split['test']))
        report=calibrate(packet,settings()['statistics'])
        self.assertFalse(report['automatically_accepted']);self.assertTrue(report['optimizer_success'])
        self.assertLess(report['metrics']['test']['brier'],report['baseline']['test']['brier'])
        self.assertEqual(len(report['test_brier_cluster_bootstrap_95_interval']),2)

    def test_missing_authorization_duplicate_and_insufficient_groups_rejected(self):
        packet=self.packet();del packet['authorization']
        with self.assertRaises(ValueError):calibrate(packet,settings()['statistics'])
        packet=self.packet();packet['observations'][1]['observation_id']='0'
        with self.assertRaises(ValueError):calibrate(packet,settings()['statistics'])
        packet=self.packet()
        for r in packet['observations']:r['player_id']='same'
        with self.assertRaises(ValueError):calibrate(packet,settings()['statistics'])


if __name__=='__main__':unittest.main()
