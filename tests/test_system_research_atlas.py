import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_forecast import fixture
from wt_atlas.common import file_hash
from wt_atlas.forecast.inventory import Inventory
from wt_atlas.system_research_atlas import assemble_subset
from wt_atlas.system_research_atlas import stage_verified_file


class SystemResearchAtlasTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.catalog,self.atlas,self.ammunition=fixture(self.root)
        self.profiles={p['name']:p for p in json.loads(self.ammunition.read_text(encoding='utf8'))}
        self.metadata=[json.loads(p.read_text(encoding='utf8')) for p in (self.atlas/'arrays').glob('*.json')]
        self.bindings={m['target_id']:{'target_sha256':m['target_sha256']} for m in self.metadata}
        self.index_path=self.root/'index.sqlite';index=Inventory(self.index_path)
        index.sync(self.atlas,self.ammunition,self.catalog,bindings=self.bindings)
        index.verify(self.atlas);index.close()
        m=self.metadata[0];self.key=(m['target_id'],m['ammo_id'],self.profiles[m['ammo_id']]['distance_m'])
        self.output=self.root/'research-atlas'

    def tearDown(self):self.temp.cleanup()

    def test_copies_verified_exact_map_with_source_receipts(self):
        report=assemble_subset([self.index_path],[self.key],self.profiles,self.bindings,self.output)
        self.assertEqual(report['maps'],1);self.assertFalse(report['publishable'])
        record=report['records'][0];copy=self.output/'arrays'/(record['map_id']+'.npz')
        self.assertEqual(file_hash(copy),record['array_sha256'])
        original=self.atlas/'arrays'/(record['map_id']+'.npz');original.write_bytes(b'changed after copy')
        self.assertEqual(file_hash(copy),record['array_sha256'])

    def test_changed_array_missing_map_and_failed_final_check_do_not_publish(self):
        m=self.metadata[0];source=self.atlas/'arrays'/(m['map_id']+'.npz');source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'source changed'):
            assemble_subset([self.index_path],[self.key],self.profiles,self.bindings,self.output)
        self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(ValueError,'Missing verified'):
            assemble_subset([self.index_path],[('missing','missing',10.)],self.profiles,self.bindings,self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_duplicate_indexes_and_changed_input_before_publication(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):
            assemble_subset([self.index_path,self.index_path],[self.key],self.profiles,self.bindings,self.output)
        def changed():raise ValueError('input changed')
        with self.assertRaisesRegex(ValueError,'input changed'):
            assemble_subset([self.index_path],[self.key],self.profiles,self.bindings,self.output,validate_inputs=changed)
        self.assertFalse(self.output.exists())

    def test_interrupted_copy_resumes_only_matching_staged_bytes(self):
        calls=[]
        def interrupted(source,destination,expected,**kwargs):
            calls.append(destination)
            if len(calls)==2:raise RuntimeError('simulated interruption')
            return stage_verified_file(source,destination,expected,**kwargs)
        with patch('wt_atlas.system_research_atlas.stage_verified_file',side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError,'simulated interruption'):
                assemble_subset([self.index_path],[self.key],self.profiles,self.bindings,self.output,resume=True)
        self.assertFalse(self.output.exists());partial=self.output.with_name('.'+self.output.name+'.partial')
        saved=next((partial/'arrays').glob('*.json'));before=saved.stat().st_mtime_ns
        result=assemble_subset([self.index_path],[self.key],self.profiles,self.bindings,self.output,resume=True)
        self.assertEqual(result['maps'],1)
        self.assertEqual((self.output/'arrays'/saved.name).stat().st_mtime_ns,before)


if __name__=='__main__':unittest.main()
