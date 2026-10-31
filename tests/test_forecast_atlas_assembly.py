from pathlib import Path
import errno
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from test_forecast import fixture
from site_fixture import load_script
from wt_atlas.common import read_json,write_json,file_hash
from wt_atlas.forecast.inventory import Inventory


class AtlasAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.catalog,self.atlas,self.ammo=fixture(self.root)
        self.extra=self.root/'extra';(self.extra/'arrays').mkdir(parents=True)
        self.meta=next((self.atlas/'arrays').glob('*.json'));m=read_json(self.meta)
        for path in (self.meta,self.atlas/m['array_file']):shutil.move(path,self.extra/'arrays'/path.name)
        self.index=Inventory(self.root/'base.sqlite');self.index.sync(self.atlas,self.ammo,self.catalog);self.index.verify(self.atlas)
        self.tool=load_script('assemble_native_atlas');self.output=self.root/'assembled'
    def tearDown(self):self.index.close();self.temp.cleanup()
    def assemble(self,**kw):return self.tool.assemble(self.root/'base.sqlite',self.ammo,[self.extra],self.output,**kw)

    def extra_index(self):
        path=self.root/'extra.sqlite';index=Inventory(path)
        try:
            index.sync(self.extra,self.ammo,self.catalog)
            self.assertEqual(index.verify(self.extra)['verified_arrays'],1)
        finally:index.close()
        return path

    def indexed_assemble(self,extra_index,**kw):
        return self.tool.assemble(self.root/'base.sqlite',self.ammo,[],self.output,extra_indexes=[extra_index],**kw)

    def test_complete_atomic_union_has_no_source_writes_and_passes_preflight(self):
        sources=[*self.atlas.rglob('*.npz'),*self.extra.rglob('*.npz')];before=[file_hash(p) for p in sources]
        r=self.assemble();self.assertEqual((r['maps'],r['new_maps']),(16,1))
        self.assertEqual(r['status'],'assembled_requires_fresh_preflight')
        self.assertEqual(before,[file_hash(p) for p in sources])
        check=Inventory(self.root/'combined.sqlite')
        try:
            check.sync(self.output,self.ammo,self.catalog)
            self.assertEqual(check.verify(self.output)['verified_arrays'],16)
        finally:check.close()

    def test_stale_metadata_or_array_leaves_output_absent(self):
        source=next((self.atlas/'arrays').glob('*.npz'));source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Base NPZ changed'):self.assemble()
        self.assertFalse(self.output.exists())

    def test_mixed_version_is_rejected_before_publication(self):
        path=next((self.extra/'arrays').glob('*.json'));m=read_json(path);m['source_version']='other';write_json(path,m)
        with self.assertRaisesRegex(ValueError,'Mixed physical'):self.assemble()
        self.assertFalse(self.output.exists())

    def test_copy_mode_survives_later_source_mutation_and_protects_existing_output(self):
        self.assertEqual(self.assemble(copy=True)['hardlinked_arrays'],0)
        source=next((self.atlas/'arrays').glob('*.npz'));destination=self.output/'arrays'/source.name
        expected=file_hash(destination);source.write_bytes(b'changed source')
        self.assertEqual(file_hash(destination),expected)
        with self.assertRaisesRegex(ValueError,'new assembly directory'):self.assemble()

    def test_verified_extra_index_is_used_without_reloading_npz_arrays(self):
        index=self.extra_index()
        with patch.object(self.tool,'verify_array',side_effect=AssertionError('unexpected reload')):
            report=self.indexed_assemble(index)
        self.assertEqual((report['maps'],report['new_maps']),(16,1))
        self.assertFalse(report['resumable'])

    def test_incomplete_or_unfingerprinted_extra_index_cannot_be_assembled(self):
        index=self.extra_index()
        db=sqlite3.connect(index)
        try:
            db.execute("UPDATE maps SET status='metadata_valid' WHERE selected=1")
            db.commit()
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'Extra indexed arrays need preflight'):
            self.indexed_assemble(index)
        db=sqlite3.connect(index)
        try:
            db.execute("UPDATE maps SET status='verified',meta_hash=NULL WHERE selected=1")
            db.commit()
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'missing source fingerprints'):
            self.indexed_assemble(index)
        self.assertFalse(self.output.exists())

    def test_resume_reuses_completed_links_after_interruption(self):
        index=self.extra_index();original=os.link;calls=[]
        def interrupt(source,destination):
            calls.append(destination)
            if len(calls)==4:raise OSError(errno.EIO,'simulated interruption')
            return original(source,destination)
        with patch.object(self.tool.os,'link',side_effect=interrupt),self.assertRaises(OSError):
            self.indexed_assemble(index,resume=True)
        self.assertFalse(self.output.exists())
        stage=self.root/'.assembled.partial'
        self.assertTrue((stage/'.wt-assembly-stage.json').is_file())
        report=self.indexed_assemble(index,resume=True)
        self.assertEqual(report['maps'],16)
        self.assertEqual(report['reused_staged_maps'],3)
        self.assertTrue(self.output.is_dir())
        self.assertFalse(stage.exists())

    def test_resume_recovers_interrupted_copy_without_publishing_partial_array(self):
        index=self.extra_index();original=shutil.copyfile;interrupted=[False]
        def partial(source,destination,*args,**kwargs):
            if str(destination).endswith('.npz.part') and not interrupted[0]:
                interrupted[0]=True;Path(destination).write_bytes(b'partial')
                raise OSError(errno.EIO,'simulated interruption')
            return original(source,destination,*args,**kwargs)
        with patch.object(self.tool.shutil,'copyfile',side_effect=partial),self.assertRaises(OSError):
            self.indexed_assemble(index,resume=True,copy=True)
        stage=self.root/'.assembled.partial'
        self.assertTrue(list((stage/'arrays').glob('*.npz.part')))
        self.assertFalse(self.output.exists())
        report=self.indexed_assemble(index,resume=True,copy=True)
        self.assertEqual(report['maps'],16)
        self.assertFalse(list(self.output.rglob('*.part')))

    def test_resume_rejects_changed_partial_metadata_and_source_index(self):
        index=self.extra_index();original=os.link;calls=[]
        def interrupt(source,destination):
            calls.append(destination)
            if len(calls)==2:raise OSError(errno.EIO,'simulated interruption')
            return original(source,destination)
        with patch.object(self.tool.os,'link',side_effect=interrupt),self.assertRaises(OSError):
            self.indexed_assemble(index,resume=True)
        stage=self.root/'.assembled.partial';metadata=next((stage/'arrays').glob('*.json'))
        original_meta=metadata.read_bytes();metadata.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Staged metadata changed'):
            self.indexed_assemble(index,resume=True)
        metadata.write_bytes(original_meta)
        self.index.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('test_changed','yes')")
        self.index.db.commit()
        with self.assertRaisesRegex(ValueError,'partial assembly does not match'):
            self.indexed_assemble(index,resume=True)
        self.assertFalse(self.output.exists())

if __name__=='__main__':unittest.main()
