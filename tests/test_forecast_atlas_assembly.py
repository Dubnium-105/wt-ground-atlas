from pathlib import Path
import shutil
import tempfile
import unittest
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

if __name__=='__main__':unittest.main()
