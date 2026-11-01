from pathlib import Path
import tempfile
import unittest

from wt_atlas.system_batch_runner import SourceGuard,report_key


class SystemBatchRunnerTests(unittest.TestCase):
    def test_source_guard_rechecks_changes_and_rejects_conflicting_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source';path.write_text('first',encoding='utf8');guard=SourceGuard()
            original=guard.add(path);guard.check();path.write_text('changed',encoding='utf8')
            with self.assertRaisesRegex(ValueError,'Pinned source changed'):guard.check()
            with self.assertRaisesRegex(ValueError,'Pinned source changed'):SourceGuard().add(path,original)

    def test_job_key_binds_every_request_and_batch_identity(self):
        identity={'plan_sha256':'one','runtime':{'numpy':'a'}};request={'system_id':'one','attacker_id':'a','ammo_id':'p'}
        self.assertEqual(report_key(identity,request),report_key(dict(identity),dict(request)))
        self.assertNotEqual(report_key(identity,request),report_key(identity,{**request,'ammo_id':'q'}))
        self.assertNotEqual(report_key(identity,request),report_key({**identity,'plan_sha256':'two'},request))


if __name__=='__main__':unittest.main()
