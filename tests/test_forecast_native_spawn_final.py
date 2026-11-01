import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import file_hash
from wt_atlas.forecast.native_spawn_final import audit_client, compose_final_stage, INT32_MAX


class NativeFinalSpawnTests(unittest.TestCase):
    def test_half_ties_follow_native_bias_instead_of_python_bankers_rounding(self):
        self.assertEqual(compose_final_stage(1, 2.5, 1), 3)
        self.assertEqual(compose_final_stage(1, 3.5, 1), 4)
        self.assertEqual(compose_final_stage(1, .5, 1), 1)

    def test_predecessor_half_is_not_promoted_by_exact_half_addition(self):
        value = math.nextafter(.5, 0.)
        self.assertEqual(math.trunc(value + .5), 1)
        self.assertEqual(compose_final_stage(1, value, 1), 0)
        self.assertEqual(compose_final_stage(1, math.nextafter(.5, 1.), 1), 1)

    def test_multipliers_apply_before_the_single_rounding(self):
        self.assertEqual(compose_final_stage(1, 1.5, 1.5), 2)
        self.assertEqual(compose_final_stage(150, 1.2, 3), 540)

    def test_native_integer_domain_including_zero_and_upper_boundary(self):
        self.assertEqual(compose_final_stage(0, 2, 3), 0)
        self.assertEqual(compose_final_stage(INT32_MAX, 1, 1), INT32_MAX)
        for base in (True, -1, 1., INT32_MAX + 1, None):
            with self.subTest(base=base), self.assertRaises(ValueError):
                compose_final_stage(base, 1, 1)

    def test_invalid_multipliers_and_overflow_are_rejected(self):
        for value in (True, -1, math.inf, math.nan, None, '1', 10**500):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compose_final_stage(1, value, 1)
            with self.subTest(value=value), self.assertRaises(ValueError):
                compose_final_stage(1, 1, value)
        for base, weapon, kind in ((INT32_MAX, 2, 1), (1, 1e308, 1e308)):
            with self.assertRaises(ValueError):
                compose_final_stage(base, weapon, kind)

    def test_review_receipt_never_promotes_static_arithmetic_to_battle_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.bin'
            data = b'header:test-function:footer'
            path.write_bytes(data)
            manifest = {'game_version': 'synthetic', 'sha256': file_hash(path), 'size_bytes': len(data),
                'ranges': [('synthetic_function', '0x7', 7, 13, hashlib.sha256(data[7:20]).hexdigest())]}
            with patch('wt_atlas.forecast.native_spawn_final.REVIEWED_CLIENT', manifest):
                report = audit_client(path, 'synthetic')
                self.assertFalse(report['publishable'])
                self.assertFalse(report['complete_base_formula_verified'])
                self.assertFalse(report['promoted_spawn_rules'])
                self.assertFalse(report['client_executed'])
                self.assertEqual(report['live_native_outputs_observed'], 0)
                self.assertEqual(report['generated_verified_final_costs'], 0)
                self.assertEqual(file_hash(path), manifest['sha256'])
                with self.assertRaisesRegex(ValueError, 'version'):
                    audit_client(path, 'different')
                path.write_bytes(data[:-1] + b'!')
                with self.assertRaisesRegex(ValueError, 'hash or size'):
                    audit_client(path, 'synthetic')
                path.write_bytes(data)
                manifest['ranges'][0] = ('bad_range', '0x7', 8, 13, manifest['ranges'][0][-1])
                with self.assertRaisesRegex(ValueError, 'range changed'):
                    audit_client(path, 'synthetic')

    def test_cli_replay_marks_supplied_inputs_unverified_and_keeps_sources(self):
        spec = importlib.util.spec_from_file_location('audit_native_spawn', ROOT / 'scripts/audit_native_spawn.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, replay, output = (root / name for name in ('client.bin', 'replay.json', 'out.json'))
            client.write_bytes(b'fixture')
            replay.write_text(json.dumps([{'base_cost': 1, 'weapon_multiplier': 2.5, 'type_multiplier': 1}]), encoding='utf-8')
            original = file_hash(replay)
            receipt = {'game_version': 'synthetic', 'source_sha256': file_hash(client),
                'complete_base_formula_verified': False, 'generated_verified_final_costs': 0, 'publishable': False}
            args = ['audit_native_spawn', '--client-exe', str(client), '--game-version', 'synthetic',
                    '--replay', str(replay), '--out', str(output)]
            with patch.object(sys, 'argv', args), patch.object(cli, 'audit_client', return_value=receipt), patch('builtins.print'):
                cli.main()
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['replay'][0]['result'], 3)
            self.assertEqual(report['replay'][0]['status'], 'supplied_inputs_not_verified_battle_cost')
            self.assertFalse(report['publishable'])
            self.assertEqual(report['generated_verified_final_costs'], 0)
            self.assertEqual(file_hash(replay), original)
            args[-1] = str(replay)
            with patch.object(sys, 'argv', args), self.assertRaisesRegex(ValueError, 'sources and output separate'):
                cli.main()


if __name__ == '__main__':
    unittest.main()
