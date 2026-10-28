"""Runtime projection must reduce research payloads without changing decisions."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from site_fixture import PROJECT, build_recommendations, create_fixture, load_script, write_json


class RuntimeProbabilityContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.projection = load_script('decision_bundle')
        cls.builder = load_script('build_web')

    def test_only_explicit_false_is_published_and_source_is_unchanged(self):
        records = [{'id': str(value), 'reference_only': value} for value in (True, False, None, 0, 'false')]
        records.append({'id': 'missing-flag'})
        source = {'schema_version': '1.0.0', 'model_version': 'test-physics', 'records': records,
                  'entries': [{'target_id': 'a', 'ammo_id': 'x'}, {'target_id': 'a', 'ammo_id': 'y'}, {'target_id': 'b'}],
                  'provenance': {'index_sha256': 'a' * 64, 'research_dump': ['omitted']},
                  'research_dump': ['omitted']}
        before = copy.deepcopy(source)
        result = self.projection.project_runtime_bundle(source, 'b' * 64)
        self.assertEqual(result['records'], [records[1]])
        self.assertEqual(result['entries'], [{'target_id': 'a'}, {'target_id': 'b'}])
        self.assertEqual(result['model_version'], 'test-physics')
        self.assertEqual(result['provenance'], {'index_sha256': 'a' * 64})
        self.assertNotIn('research_dump', result)
        self.assertEqual(result['runtime_projection']['omitted_record_count'], 5)
        self.assertEqual(source, before)

    def test_research_growth_does_not_reach_site_and_decisions_remain_identical(self):
        node = os.environ.get('GROUNDOPS_NODE') or shutil.which('node')
        with tempfile.TemporaryDirectory(prefix='wt-runtime-probabilities-') as temporary:
            root = create_fixture(Path(temporary))
            config_path = root / 'config/model.json'
            aim_script = """
import fs from 'node:fs';
import {aimContextKey} from './shared/engine.mjs';
const config = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
process.stdout.write(aimContextKey({}, config));
"""
            aim_key = subprocess.run([node, '--input-type=module', '-e', aim_script, str(config_path)],
                                     cwd=PROJECT, check=True, capture_output=True, text=True).stdout
            exact = {
                'id': 'verified-test-record', 'target_id': 'synthetic_ussr_0',
                'configuration_id': 'synthetic_ussr_0', 'ammo_id': 'synthetic_usa_0_round',
                'ammo_profile_sha256': 'synthetic-test-only', 'physical_distance_m': 1000,
                'angle_deg': 0, 'geometry_version': 'synthetic-geometry',
                'configuration_version': 'synthetic-materials', 'model_version': 'synthetic-physics',
                'source_version': 'synthetic-test-only', 'aim_context_key': aim_key,
                'reference_only': False, 'mean': 0.8, 'lower': 0.7, 'upper': 0.9, 'coverage': 1,
                'source_entry_id': 'synthetic-entry',
                'verification_evidence': {'test_fixture': True, 'note': 'Retain evidence unchanged'},
            }
            references = [{**exact, 'id': f'research-{i}', 'reference_only': True, 'mean': 0.99,
                           'research_detail': 'R' * 4096} for i in range(512)]
            source = {
                'schema_version': '1.0.0', 'model_version': 'synthetic-physics',
                'decision_model_version': json.loads(config_path.read_text())['model_version'],
                'records': [*references, exact],
                'entries': [{'id': f'research-{i}', 'target_id': 'synthetic_ussr_0', 'shards': ['unused'] * 30}
                            for i in range(512)],
                'provenance': {'index_sha256': 'a' * 64, 'source_version': 'synthetic-test-only'},
                'calibrated_game_probability': False,
            }
            source_path = root / 'data/snapshots/decision-probabilities.json'
            write_json(source_path, source)
            original_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            self.assertGreater(source_path.stat().st_size, 2 * 1024**2)
            build_recommendations(root)
            with contextlib.redirect_stdout(io.StringIO()):
                self.builder.build(root)
            site = root / 'dist/site'
            manifest = json.loads((site / 'data/manifest.json').read_text())
            published_path = site / manifest['decision_probabilities']['url']
            runtime = json.loads(published_path.read_text())
            self.assertLess(published_path.stat().st_size, 4096)
            self.assertEqual(runtime['records'], [exact])
            self.assertEqual(runtime['entries'], [{'target_id': 'synthetic_ussr_0'}])
            self.assertEqual(runtime['runtime_projection']['source_bundle_sha256'], original_hash)
            self.assertEqual(runtime['runtime_projection']['original_record_count'], 513)
            self.assertEqual(runtime['runtime_projection']['published_record_count'], 1)
            self.assertEqual(runtime['runtime_projection']['omitted_record_count'], 512)
            self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), original_hash)

            parity_script = """
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {evaluatePair, evaluateLineup, recommendNation} from './shared/engine.mjs';
const root = process.argv[1], publishedFile = process.argv[2];
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const config = read(path.join(root, 'config/model.json'));
const vehicles = read(path.join(root, 'data/snapshots/catalog.json')).vehicles;
const original = read(path.join(root, 'data/snapshots/decision-probabilities.json'));
const published = read(publishedFile);
const attacker = vehicles.find(v => v.id === 'synthetic_usa_0');
const target = vehicles.find(v => v.id === 'synthetic_ussr_0');
assert.equal(evaluatePair(attacker, target, {}, config, published).quality, 'conditional_layered_model');
for (const context of [{}, {physical_distance_m:2000}, {skill:1}, {angle_deg:90}, {ammo_id:'unowned'}]) {
  assert.deepEqual(evaluatePair(attacker, target, context, config, published), evaluatePair(attacker, target, context, config, original));
  assert.deepEqual(evaluateLineup(vehicles, [attacker.id], context, config, published), evaluateLineup(vehicles, [attacker.id], context, config, original));
  assert.deepEqual(recommendNation(vehicles, 'usa', context, config, published), recommendNation(vehicles, 'usa', context, config, original));
}
console.log('Exact, unmatched and unowned-ammo results match the original research bundle.');
"""
            subprocess.run([node, '--input-type=module', '-e', parity_script, str(root), str(published_path)],
                           cwd=PROJECT, check=True, capture_output=True, text=True)

            # Even an omitted research record is still part of input identity:
            # a new source requires rebuilt recommendations before publication.
            before = (site / 'data/manifest.json').read_bytes()
            source['records'][0]['research_detail'] = 'changed research provenance'
            write_json(source_path, source)
            with self.assertRaises(ValueError), contextlib.redirect_stdout(io.StringIO()):
                self.builder.build(root)
            self.assertEqual((site / 'data/manifest.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
