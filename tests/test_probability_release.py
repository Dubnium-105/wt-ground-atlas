"""Storage and publication contracts using tiny, explicitly synthetic arrays."""
from __future__ import annotations

import copy
import gzip
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from wt_atlas.common import file_hash, read_json, stage_verified_file, write_json
from wt_atlas.probability import decode_view, export_atlas

ROOT = Path(__file__).resolve().parents[1]
CONFIG = read_json(ROOT / 'config/pipeline.json')
spec = importlib.util.spec_from_file_location('prepare_probability_release', ROOT / 'scripts/prepare_probability_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def make_input(root):
    source = root / 'input'
    (source / 'arrays').mkdir(parents=True)
    write_json(source / 'provenance/plan.json', {'width': 2, 'height': 2, 'angles_deg': [0, 90], 'pixel_pitch_m': .1})
    write_json(source / 'provenance/ammunition.json', [{'name': 'fixture', 'mode': 'KE', 'distance_m': 500}])
    write_json(source / 'provenance/model_scope.json', {'fixture_only': True})
    write_json(source / 'format.json', {'per_ammunition_arrays': {'status': {'values': CONFIG['status_legend']}}})
    probability = np.array([[[0, .25], [np.nan, 1]], [[.1, .7], [np.nan, .5]]], dtype=np.float32)
    state = np.array([[[3, 3], [2, 3]], [[3, 3], [0, 3]]], dtype=np.uint8)
    arrays = source / 'arrays/fixture.fixture.npz'
    np.savez_compressed(arrays, penetration_probability=probability, status=state, material_response_flags=np.zeros_like(state))
    summary = {'algorithm': 'synthetic-fixture', 'client_version': 'fixture-only', 'datamine_commit': 'fixture-only',
               'groups': [{'vehicle': 'fixture', 'grp_sha256': 'a' * 64, 'groups': [
                   {'array_file': 'arrays/fixture.fixture.npz', 'array_sha256': file_hash(arrays)}]}]}
    write_json(source / 'summary.json', summary)
    return source, probability


class PublicationContracts(unittest.TestCase):
    def test_export_preserves_values_and_reuses_verified_compression(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, probability = make_input(root)
            output = root / 'large-data'
            export_atlas(source, output, CONFIG)
            index_before = (output / 'index.json').read_bytes()
            index = read_json(output / 'index.json')
            for ai, shard in enumerate(index['entries'][0]['shards']):
                compressed = output / shard['gzip_url']
                os.utime(compressed, ns=(1_000_000_000, 1_000_000_000))
                raw = gzip.decompress(compressed.read_bytes())
                restored, _, _ = decode_view(raw, 2, 2, CONFIG['export']['quantization_max'], CONFIG['export']['unknown_value'])
                np.testing.assert_allclose(restored, probability[ai], atol=CONFIG['export']['max_export_error'], equal_nan=True)
            repeated = export_atlas(source, output, CONFIG)
            self.assertEqual(repeated['reused_shards'], 2)
            self.assertEqual(repeated['reused_gzip_shards'], 2)
            self.assertEqual((output / 'index.json').read_bytes(), index_before)
            for shard in index['entries'][0]['shards']:
                self.assertEqual((output / shard['gzip_url']).stat().st_mtime_ns, 1_000_000_000)
            broken = output / index['entries'][0]['shards'][0]['gzip_url']
            broken.write_bytes(b'broken')
            self.assertEqual(export_atlas(source, output, CONFIG)['reused_gzip_shards'], 1)
            release.prepare_release(output)

    def test_invalid_input_never_replaces_published_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _ = make_input(root)
            output = root / 'probability'
            export_atlas(source, output, CONFIG)
            original = (output / 'index.json').read_bytes()
            strict = copy.deepcopy(CONFIG)
            strict['export']['max_export_error'] = 0
            with self.assertRaisesRegex(ValueError, 'Quantization budget'):
                export_atlas(source, output, strict)
            self.assertEqual((output / 'index.json').read_bytes(), original)
            summary = read_json(source / 'summary.json')
            summary['groups'][0]['groups'][0].pop('array_sha256')
            write_json(source / 'summary.json', summary)
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                export_atlas(source, output, CONFIG)
            self.assertEqual((output / 'index.json').read_bytes(), original)

    def test_release_lists_only_verified_references_and_indexes_last(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _ = make_input(root)
            output = root / 'probability'
            export_atlas(source, output, CONFIG)
            (output / 'old-shard.bin').write_bytes(b'stale and unreferenced')
            index = read_json(output / 'index.json')
            preview = 'shards/fixture/fixture/preview.png'
            (output / preview).write_bytes(b'synthetic preview placeholder')
            index['entries'][0]['shards'][0]['preview_url'] = preview
            write_json(output / 'index.json', index)
            (output / 'index.json.gz').write_bytes(gzip.compress((output / 'index.json').read_bytes(), mtime=0))
            report_path, listing = root / 'report.json', root / 'files.txt'
            result = release.prepare_release(output, report_path, listing)
            self.assertEqual(result['release_id'], file_hash(output / 'index.json'))
            self.assertEqual(result['file_count'], 7)
            names = [item['path'] for item in result['files']]
            self.assertIn(preview, names)
            self.assertNotIn('old-shard.bin', names)
            self.assertEqual(names[-2:], ['index.json.gz', 'index.json'])
            self.assertEqual(listing.read_text(encoding="utf-8").splitlines(), names)
            self.assertEqual(read_json(report_path), result)
            self.assertEqual(result['total_bytes'], sum((output / name).stat().st_size for name in names))

    def test_release_rejects_corruption_and_preserves_previous_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _ = make_input(root)
            output, report = root / 'probability', root / 'report.json'
            export_atlas(source, output, CONFIG)
            release.prepare_release(output, report)
            original = report.read_bytes()
            shard = read_json(output / 'index.json')['entries'][0]['shards'][0]
            (output / shard['url']).write_bytes(b'x' * shard['bytes'])
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                release.prepare_release(output, report)
            self.assertEqual(report.read_bytes(), original)

    def test_release_rejects_escape_symlinks_and_wrong_gzip_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _ = make_input(root)
            output = root / 'probability'
            export_atlas(source, output, CONFIG)
            index = read_json(output / 'index.json')
            shard = index['entries'][0]['shards'][0]
            with self.assertRaisesRegex(ValueError, 'safe relative'):
                release._local_file(output, '../input/summary.json')
            with self.assertRaisesRegex(ValueError, 'outside'):
                release.prepare_release(output, output / 'index.json')
            binary = output / shard['url']
            external = root / 'external.bin'
            external.write_bytes(binary.read_bytes())
            binary.unlink()
            try:
                binary.symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest('Filesystem does not support symlinks')
            with self.assertRaisesRegex(ValueError, 'escapes|symlink'):
                release.prepare_release(output)
            binary.unlink()
            binary.write_bytes(external.read_bytes())
            compressed = output / shard['gzip_url']
            compressed.write_bytes(gzip.compress(b'x' * shard['bytes'], mtime=0))
            shard['gzip_sha256'] = file_hash(compressed)
            shard['gzip_bytes'] = compressed.stat().st_size
            write_json(output / 'index.json', index)
            with self.assertRaisesRegex(ValueError, 'Gzip does not match'):
                release.prepare_release(output)


class StagingContracts(unittest.TestCase):
    def test_hardlink_staging_and_atomic_result_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / 'result.npz', root / 'stage/result.npz'
            source.write_bytes(b'verified result')
            result = stage_verified_file(source, destination, file_hash(source))
            self.assertIn(result, ['linked', 'copied'])
            if result == 'linked':
                self.assertTrue(os.path.samefile(source, destination))
            self.assertEqual(stage_verified_file(source, destination, file_hash(source)), 'reused')
            replacement = root / 'new-result.npz'
            replacement.write_bytes(b'new result')
            replacement.replace(source)
            self.assertEqual(destination.read_bytes(), b'verified result')

    def test_cross_device_copy_and_failure_do_not_corrupt_previous_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / 'result.npz', root / 'stage/result.npz'
            source.write_bytes(b'verified result')
            with patch('wt_atlas.common.os.link', side_effect=OSError('different device')):
                self.assertEqual(stage_verified_file(source, destination, file_hash(source)), 'copied')
            source.write_bytes(b'corrupt input')
            with self.assertRaisesRegex(ValueError, 'hash changed'):
                stage_verified_file(source, destination, file_hash(destination))
            self.assertEqual(destination.read_bytes(), b'verified result')
            self.assertEqual(list(destination.parent.glob('*.part')), [])


if __name__ == '__main__':
    unittest.main()
