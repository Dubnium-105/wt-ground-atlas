"""Deployment boundary tests using tiny, explicitly synthetic inputs."""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from site_fixture import create_fixture, load_script, write_json


def hashes(directory: Path) -> dict:
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob('*') if p.is_file()}


class SiteBuildContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = load_script('build_web')
        cls.packager = load_script('package_release')
        cls.template_directory = tempfile.TemporaryDirectory(prefix='wt-site-template-')
        cls.template = create_fixture(Path(cls.template_directory.name))

    @classmethod
    def tearDownClass(cls):
        cls.template_directory.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='wt-site-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'project'
        shutil.copytree(self.template, self.root)
        self.site = self.root / 'dist/site'

    def build(self, **options):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.builder.build(self.root, **options)

    def assert_local_descriptors(self, value):
        if isinstance(value, dict):
            url = value.get('url')
            if isinstance(url, str) and url.startswith('data/'):
                content = (self.site / url).read_bytes()
                if value.get('sha256'):
                    self.assertEqual(hashlib.sha256(content).hexdigest(), value['sha256'])
                if value.get('gzip_url'):
                    self.assertEqual(gzip.decompress((self.site / value['gzip_url']).read_bytes()), content)
            for item in value.values():
                self.assert_local_descriptors(item)
        elif isinstance(value, list):
            for item in value:
                self.assert_local_descriptors(item)

    def test_default_site_builds_without_probability_dataset_and_preserves_sources(self):
        shutil.rmtree(self.root / 'data/exports')
        source_before = hashes(self.root / 'web')
        self.build()
        manifest = json.loads((self.site / 'data/manifest.json').read_text())
        self.assertNotIn('probability', manifest)
        self.assertTrue((self.site / 'index.html').is_file())
        self.assertEqual((self.site / 'shared/engine.mjs').read_bytes(), (self.root / 'shared/engine.mjs').read_bytes())
        self.assertEqual(hashes(self.root / 'web'), source_before)
        self.assertFalse((self.root / 'web/data').exists())
        self.assertFalse((self.site / 'data/probability').exists())
        self.assert_local_descriptors(manifest)

    def test_external_mode_excludes_an_eleven_gibibyte_shard(self):
        # Sparse file has realistic apparent size without allocating 11 GiB.
        archive = self.root / 'data/exports/probability/shards/unused-huge.bin'
        with archive.open('wb') as handle:
            handle.truncate(11 * 1024**3)
        self.build(probability_mode='external', probability_base_url='https://data.example.test/releases/fixture')
        manifest = json.loads((self.site / 'data/manifest.json').read_text())
        self.assertEqual(manifest['probability']['url'], 'https://data.example.test/releases/fixture/index.json')
        self.assertFalse((self.site / 'data/probability').exists())
        self.assertLess(sum(p.stat().st_size for p in self.site.rglob('*') if p.is_file()), 2 * 1024**2)
        self.assertEqual(archive.stat().st_size, 11 * 1024**3)

    def test_embedded_mode_copies_only_index_references(self):
        export = self.root / 'data/exports/probability'
        index = json.loads((export / 'index.json').read_text())
        orphan = export / 'shards/unreferenced.bin'
        orphan.write_bytes(b'unreferenced previous release')
        self.build(probability_mode='embedded')
        manifest = json.loads((self.site / 'data/manifest.json').read_text())
        self.assertEqual(manifest['probability']['url'], 'data/probability/index.json')
        self.assertFalse((self.site / 'data/probability/shards/unreferenced.bin').exists())
        for entry in index['entries']:
            for shard in entry['shards']:
                for key in ('url', 'gzip_url'):
                    self.assertEqual((self.site / 'data/probability' / shard[key]).read_bytes(),
                                     (export / shard[key]).read_bytes())
        self.assert_local_descriptors(manifest)

    def test_rebuild_is_identical_and_removes_old_embedded_dataset(self):
        self.build(probability_mode='embedded')
        first = hashes(self.site)
        self.build(probability_mode='embedded')
        self.assertEqual(hashes(self.site), first)
        self.build(probability_mode='disabled')
        self.assertFalse((self.site / 'data/probability').exists())

    def test_stale_recommendations_do_not_replace_previous_site(self):
        self.build()
        before = hashes(self.site)
        catalog_path = self.root / 'data/snapshots/catalog.json'
        catalog = json.loads(catalog_path.read_text())
        catalog['vehicles'][0]['name'] += ' changed after computation'
        write_json(catalog_path, catalog)
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual(hashes(self.site), before)

    def test_site_and_per_file_budgets_fail_before_publication(self):
        self.build()
        before = hashes(self.site)
        settings_path = self.root / 'config/deployment.json'
        for budgets in ({'site_bytes': 1024, 'file_bytes': 26214400},
                        {'site_bytes': 104857600, 'file_bytes': 1024}):
            with self.subTest(budgets=budgets):
                settings = json.loads(settings_path.read_text())
                settings['budgets'] = budgets
                write_json(settings_path, settings)
                with self.assertRaises(ValueError):
                    self.build()
                self.assertEqual(hashes(self.site), before)

    def test_external_origin_and_source_output_are_validated(self):
        for base_url in ('relative/path', 'file:///tmp/probability', 'https://name:secret@example.test/release',
                         'https://example.test/release?', 'https://example.test/release#',
                         'https://example.test:bad/release', 'https://example.test:65536/release'):
            with self.subTest(base_url=base_url):
                with self.assertRaises(ValueError):
                    self.build(probability_mode='external', probability_base_url=base_url)
        before = hashes(self.root / 'web')
        with self.assertRaises(ValueError):
            self.build(output=self.root / 'web')
        self.assertEqual(hashes(self.root / 'web'), before)

    def test_packaging_rejects_a_missing_browser_codec(self):
        self.build()
        (self.site / 'shared/probability-codec.mjs').unlink()
        output = Path(self.directory.name) / 'broken-site.zip'
        with self.assertRaises((FileNotFoundError, ValueError)):
            self.packager.package(self.root, output)
        self.assertFalse(output.exists())

    def test_public_audit_does_not_include_the_data_upload_inventory(self):
        release = {
            'release_id': 'a' * 64, 'index_sha256': 'a' * 64,
            'file_count': 100000, 'total_bytes': 11 * 1024**3,
            'all_referenced_hashes_verified': True,
            'files': [{'path': 'shards/private-upload-inventory.bin', 'bytes': 123, 'sha256': 'b' * 64}],
        }
        write_json(self.root / 'reports/probability-release.json', release)
        manifest = self.build(probability_mode='external', probability_base_url='https://data.example.test/version/')
        report = json.loads((self.site / manifest['probability_report']['url']).read_text())
        self.assertEqual(report['probability-release']['release_id'], release['release_id'])
        self.assertEqual(report['probability-release']['total_bytes'], release['total_bytes'])
        self.assertNotIn('files', report['probability-release'])
        self.assertNotIn('private-upload-inventory', json.dumps(report))

    def test_archive_contains_only_deployable_site_and_verifiable_hashes(self):
        (self.root / '.env').write_text('FIXTURE_ONLY=not-a-real-secret\n')
        (self.root / 'unrelated-local-file.txt').write_text('must not be published')
        self.build()
        output = Path(self.directory.name) / 'site.zip'
        with contextlib.redirect_stdout(io.StringIO()):
            self.packager.package(self.root, output)
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            self.assertIn('index.html', names)
            self.assertIn('FILE_INTEGRITY.sha256', names)
            self.assertNotIn('.wt-atlas-site', names)
            self.assertNotIn('.env', names)
            self.assertNotIn('unrelated-local-file.txt', names)
            self.assertFalse(any(name.startswith(('backend/', 'config/', 'data/exports/', 'data/snapshots/')) for name in names))
            checked = set()
            for line in archive.read('FILE_INTEGRITY.sha256').decode().splitlines():
                expected, name = line.split('  ', 1)
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
                checked.add(name)
            self.assertEqual(checked, names - {'FILE_INTEGRITY.sha256'})


if __name__ == '__main__':
    unittest.main()
