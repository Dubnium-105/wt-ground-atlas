"""Behavioral safeguards for offline inputs; fixtures are not game assertions."""
from pathlib import Path
import copy
import json
import tempfile
import unittest
import numpy as np
from wt_atlas.common import read_json, write_json, file_hash
from wt_atlas.probability import validate_arrays, encode_view, decode_view, physical_cache_key, export_atlas
from wt_atlas.cpu_model import PreparedLayers
from wt_atlas.jobs import acquire, JobStore, _validated_url

ROOT = Path(__file__).resolve().parents[1]
CONFIG = read_json(ROOT / 'config/pipeline.json')


def material(kind, thickness=0, solid=False):
    if kind == 'unknown':
        return {'node': 0, 'part_id': 0, 'loaded_class': None, 'class_flags': None, 'behavior_flags': 0, 'decoded_flags': None, 'source_armor_classes': []}
    if kind == 'protected':
        return {'node': 0, 'part_id': 1, 'loaded_class': None, 'class_flags': 4, 'behavior_flags': 0, 'decoded_flags': {}, 'source_armor_classes': []}
    return {'node': 0, 'part_id': 0, 'class_flags': 2, 'behavior_flags': 4 if solid else 0,
            'decoded_flags': {'allowRicochet': False}, 'source_armor_classes': [],
            'loaded_class': {'scalars': {'slopeMinCaliber': 0}, 'armor': {
                'enabled': True, 'armorThickness': thickness, 'armorQuality': 1,
                'minSolidArmorThickness': 0, 'ignoreSolidDimension': False, 'armorThrough': 0,
                'arrays': {key: {'values': [value]} for key, value in [('ArmorQuality', 1), ('ThicknessEquivalentMax', 1e9), ('EffectiveThicknessMax', 1e9)]}}}}


def make_layers(directory, rows, materials, n):
    packet = directory / 'fixture.layers.npz'
    arrays = {key: np.asarray([row[index] for row in rows], np.int32 if key in ['ray_id', 'node'] else np.float32)
              for index, key in enumerate(['ray_id', 'node', 'entry_m', 'exit_m', 'incidence_cos', 'extra_entry_m'])}
    np.savez_compressed(packet, **arrays)
    meta = {'vehicle': 'fixture', 'all_inward_node_intervals_retained': True,
            'plan': {'width': n, 'height': 1, 'angles_deg': [17], 'pixel_pitch_m': 0.04},
            'materials': materials, 'arrays_sha256': file_hash(packet)}
    metadata = directory / 'fixture.layers.json'; write_json(metadata, meta)
    return metadata


class ProbabilityContracts(unittest.TestCase):
    def test_quantization_retains_zero_unknown_and_flags(self):
        p = np.array([[0.0, np.nan, 0.3333333, 1.0]], np.float32)
        s = np.array([[3, 2, 3, 3]], np.uint8); f = np.array([[0, 9, 2, 1]], np.uint8)
        exp = CONFIG['export']
        blob, error, _, _ = encode_view(p, s, f, exp['quantization_max'], exp['unknown_value'])
        restored, state, flags = decode_view(blob, p.shape[1], p.shape[0], exp['quantization_max'], exp['unknown_value'])
        self.assertEqual(restored[0, 0], 0); self.assertTrue(np.isnan(restored[0, 1]))
        np.testing.assert_array_equal(s, state); np.testing.assert_array_equal(f, flags)
        self.assertLessEqual(error, exp['max_export_error'])

    def test_dynamic_grid_and_angle_order(self):
        p = np.full((2, 3, 7), np.nan, np.float32); s = np.zeros_like(p, np.uint8)
        plan = {'width': 7, 'height': 3, 'angles_deg': [270, 17], 'pixel_pitch_m': 0.04}
        validate_arrays(p, s, s, plan)
        with self.assertRaises(ValueError): validate_arrays(p, s, s, {**plan, 'width': 8})
        with self.assertRaises(ValueError): validate_arrays(np.nan_to_num(p), s, s, plan)

    def test_physics_key_excludes_br_but_includes_exact_conditions(self):
        identity = {key: key for key in ['target_id', 'configuration_id', 'ammo_id', 'ammo_profile_sha256', 'geometry_version', 'configuration_version', 'model_version', 'source_version']}
        identity.update(physical_distance_m=500, angles_deg=[0, 17], width=7, height=3, pixel_pitch_m=.04)
        baseline = physical_cache_key(identity)
        self.assertEqual(baseline, physical_cache_key({**identity, 'br': 10.0}))
        for field, value in [('ammo_id', 'different'), ('physical_distance_m', 1000), ('geometry_version', 'different'), ('angles_deg', [17, 0])]:
            self.assertNotEqual(baseline, physical_cache_key({**identity, field: value}))

    def test_hash_checked_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'provenance').mkdir(); (root / 'arrays').mkdir()
            write_json(root / 'provenance/plan.json', {'width': 1, 'height': 1, 'angles_deg': [0], 'pixel_pitch_m': .1})
            write_json(root / 'provenance/ammunition.json', [{'name': 'test', 'mode': 'KE'}])
            write_json(root / 'provenance/model_scope.json', {})
            write_json(root / 'format.json', {'per_ammunition_arrays': {'status': {'values': CONFIG['status_legend']}}})
            np.savez(root / 'arrays/test.test.npz', penetration_probability=np.zeros((1, 1, 1)), status=np.full((1, 1, 1), 3), material_response_flags=np.zeros((1, 1, 1)))
            write_json(root / 'summary.json', {'groups': [{'vehicle': 'test', 'groups': [{'array_file': 'arrays/test.test.npz', 'array_sha256': 'wrong'}]}]})
            with self.assertRaisesRegex(ValueError, 'hash mismatch'): export_atlas(root, root / 'public', CONFIG)


class CPUModelContracts(unittest.TestCase):
    def setUp(self):
        self.ammo = {'name': 'fixture', 'mode': 'CE', 'mechanism_id': 0, 'damage_caliber_mm': 10,
                     'penetration_mm': 50, 'dispersion': .1, 'air_loss_mm_per_m': 0,
                     'slope_rows': [], 'ricochet_rows': [], 'normalized_distance_curve': [], 'distance_m': 0}

    def test_background_no_inner_unknown_and_true_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            # ray0 empty; ray1 armor but no inner; ray2 unknown+inner; ray3 thick armor+inner.
            rows = [(1, 0, 1, 1.1, 1, np.inf), (2, 1, 1, np.inf, 1, np.inf), (2, 2, 2, 2.1, 1, np.inf),
                    (3, 0, 1, 1.1, 1, np.inf), (3, 2, 2, 2.1, 1, np.inf)]
            m = PreparedLayers(make_layers(Path(tmp), rows, [material('armor', 100), material('unknown'), material('protected')], 4), CONFIG)
            probability, charge, state, flags = m.calculate(self.ammo)
            np.testing.assert_array_equal(state.ravel(), [0, 1, 2, 3])
            self.assertTrue(np.isnan(probability.ravel()[:3]).all()); self.assertEqual(probability.ravel()[3], 0)
            self.assertEqual(charge.ravel()[3], 100)

    def test_solid_missing_exit_is_unknown_not_infinite_thickness(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [(0, 0, 1, np.inf, 1, np.inf), (0, 1, 2, 2.1, 1, np.inf)]
            m = PreparedLayers(make_layers(Path(tmp), rows, [material('armor', 100, solid=True), material('protected')], 1), CONFIG)
            p, charge, state, _ = m.calculate(self.ammo)
            self.assertEqual(state.item(), 2); self.assertTrue(np.isnan(p.item())); self.assertTrue(np.isnan(charge.item()))

    def test_ammo_specific_and_distance_label_does_not_fake_ballistics(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [(0, 0, 1, 1.1, 1, np.inf), (0, 1, 2, 2.1, 1, np.inf)]
            m = PreparedLayers(make_layers(Path(tmp), rows, [material('armor', 100), material('protected')], 1), CONFIG)
            low = m.calculate(self.ammo)[0].item()
            high = m.calculate({**self.ammo, 'name': 'other', 'penetration_mm': 200})[0].item()
            distance_only = m.calculate({**self.ammo, 'distance_m': 1500})[0].item()
            self.assertEqual(low, distance_only); self.assertLess(low, high)

    def test_bad_ray_id_cannot_map_to_wrong_pixel(self):
        with tempfile.TemporaryDirectory() as tmp:
            metadata = make_layers(Path(tmp), [(9, 0, 1, 2, 1, np.inf)], [material('protected')], 1)
            with self.assertRaisesRegex(ValueError, 'outside metadata'): PreparedLayers(metadata, CONFIG)


class AcquisitionContracts(unittest.TestCase):
    def test_content_hash_deduplicates_renamed_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'client'; source.mkdir()
            (source / 'one.grp').write_bytes(b'fixture'); (source / 'renamed.grp').write_bytes(b'fixture')
            config = copy.deepcopy(CONFIG)
            config['paths']['work'] = 'work'; config['source']['path'] = str(source)
            result = acquire(root, config)
            self.assertEqual(result['jobs'], 1)
            self.assertEqual(acquire(root, config)['jobs'], 1)
            self.assertEqual(JobStore(root, config).overview()['status_counts'], {'acquired': 1})

    def test_bad_profile_is_isolated_without_rejecting_valid_registry(self):
        from wt_atlas.jobs import profile_registry
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); directory = root / 'profiles'; directory.mkdir()
            digest = hashlib.sha256(b'fixture').hexdigest()
            write_json(directory / 'good.json', {'vehicle': 'fixture', 'grp_sha256': digest, 'materials': []})
            (directory / 'broken.json').write_text('{broken')
            config = copy.deepcopy(CONFIG); config['paths']['profiles'] = 'profiles'
            registry, issues = profile_registry(root, config)
            self.assertIn(digest, registry); self.assertEqual(len(issues), 1)

    def test_export_rejects_old_physical_conditions_before_publishing(self):
        from wt_atlas.jobs import export_completed
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config = copy.deepcopy(CONFIG)
            config['paths']['vendor'] = str(ROOT / CONFIG['paths']['vendor'])
            config['paths']['profiles'] = str(ROOT / CONFIG['paths']['profiles'])
            config['paths']['work'] = 'work'
            profile = read_json(next((ROOT / CONFIG['paths']['profiles']).glob('*.json')))
            store = JobStore(root, config)
            store.update(profile['grp_sha256'], status='complete', physical_cache_key='old-model-key', results=[{'file': 'old.npz'}])
            with self.assertRaisesRegex(ValueError, 'Stale physical job cache'):
                export_completed(root, config, 'public')
            self.assertFalse((root / 'public').exists())

    def test_download_hosts_and_embedded_credentials_not_implicit(self):
        source = {'allowed_hosts': ['example.org']}
        self.assertEqual(_validated_url('https://example.org/file', source), 'https://example.org/file')
        for url in ['http://127.0.0.1/file', 'file:///tmp/file', 'https://name:secret@example.org/file']:
            with self.assertRaises(ValueError): _validated_url(url, source)


class GeometryAndBindingContracts(unittest.TestCase):
    def test_source_grid_is_not_expanded_by_float_roundoff(self):
        from wt_atlas.geometry import make_plan
        vendor = ROOT / CONFIG['paths']['vendor']
        source = read_json(vendor / 'data/view.json')
        plan = make_plan(vendor, {}, CONFIG)
        self.assertEqual(plan['width'], source['width'])
        self.assertEqual(plan['height'], source['height'])

    def test_nested_damageparts_fields_preserve_origins_without_faking_runtime(self):
        from unittest.mock import patch
        from wt_atlas.bindings import damageparts_audit
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); grp = root / 'input.grp'; grp.write_bytes(b'fixture')
            unit = root / 'unit.json'
            write_json(unit, {'DamageParts': {'armorClass': 'parent', 'group': {'armorThickness': 90,
                        'plate_dm': {'armorThickness': 30}}}})
            decoded = [{'resource': {'name': 'collision'}, 'parsed': {'nodes': [
                {'index': 0, 'name': 'plate_dm', 'behavior_flags': 3},
                {'index': 1, 'name': 'missing', 'behavior_flags': 3}]}}]
            with patch('wt_atlas.bindings.decode_grp', return_value=decoded):
                report = damageparts_audit(grp, unit, root, root / 'audit.json', {})
            self.assertFalse(report['physics_profile_ready'])
            nodes = report['resources'][0]['nodes']
            self.assertEqual(nodes[0]['candidates'][0]['effective_source_fields']['armorThickness'], 30)
            self.assertEqual(nodes[0]['candidates'][0]['effective_source_fields']['armorClass'], 'parent')
            self.assertEqual(nodes[1]['binding_status'], 'unmapped')


if __name__ == '__main__':
    unittest.main()
