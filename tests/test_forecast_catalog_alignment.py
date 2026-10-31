"""A shared route must not silently change the input catalog's BR rules."""
from pathlib import Path
import sys
import tempfile
import unittest
from test_forecast import ROOT
from site_fixture import create_fixture, load_script
from wt_atlas.forecast.build import package_site
from wt_atlas.forecast.data import canonical, file_hash, read_json
sys.path.insert(0,str(ROOT/'scripts'))
from build_product import compose, read_published_json
from build_web import write_json


class CatalogAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.project=create_fixture(self.root/'project')
        load_script('build_web').build(self.project)
        self.research=self.project/'dist/site'
        self.sha=file_hash(self.project/'data/snapshots/catalog.json')
        self.forecast=self.root/'forecast';self.output=self.root/'product'
        package_site(self.forecast,{'build_key':'a'*64,'inputs':{'catalog':self.sha}}, {}, [], ROOT)

    def tearDown(self):self.tmp.cleanup()

    def test_same_catalog_and_older_manifest_use_verified_decision_provenance(self):
        result=compose(self.forecast,self.output,self.research)
        self.assertEqual(result['research']['catalog_alignment'],
                         {'status':'verified_same_input_catalog','catalog_sha256':self.sha})
        manifest=read_json(self.research/'data/manifest.json');manifest.pop('catalog_sha256')
        (self.research/'data/manifest.json').write_text(canonical(manifest),encoding='utf-8')
        self.assertEqual(compose(self.forecast,self.output,self.research)['research']['catalog_alignment']['catalog_sha256'],self.sha)

    def test_changed_br_catalog_cannot_replace_existing_product(self):
        compose(self.forecast,self.output,self.research)
        previous=(self.output/'index.json').read_bytes()
        catalog=self.project/'data/snapshots/catalog.json';value=read_json(catalog)
        value['vehicles'][0]['br']['current']=9.7
        catalog.write_text(canonical(value),encoding='utf-8')
        package_site(self.forecast,{'build_key':'b'*64,'inputs':{'catalog':file_hash(catalog)}}, {}, [], ROOT)
        with self.assertRaisesRegex(ValueError,'catalog mismatch'):
            compose(self.forecast,self.output,self.research)
        self.assertEqual((self.output/'index.json').read_bytes(),previous)

    def test_matching_manifest_cannot_hide_a_different_decision_catalog(self):
        manifest=read_json(self.research/'data/manifest.json')
        key=next(iter(manifest['recommendations']))
        payload=read_published_json(self.research,manifest['recommendations'][key])
        payload['data_provenance']['catalog_sha256']='b'*64
        manifest['recommendations'][key]=write_json(self.research,'data/recommendations/different.json',payload)
        (self.research/'data/manifest.json').write_text(canonical(manifest),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'catalog mismatch'):
            compose(self.forecast,self.output,self.research)
        self.assertFalse(self.output.exists())


if __name__=='__main__':unittest.main()
