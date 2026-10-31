import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from wt_atlas.remaining_ammunition import reconcile


class RemainingAmmunitionTests(unittest.TestCase):
    def setUp(self):
        self.gaps = {'source_version': '1', 'ground_ammunition': {'vehicles_without_any_exact_owned_profile': ['b', 'a']}}
        self.summary = {'catalog_sha256': 'abc', 'without_owned_supported_source_profile': 2,
                        'unresolved_selection_records': 2, 'vehicle_counts_by_raw_reason': {'missing': 1, 'other': 1}}
        self.recovery = {'source_version': '1', 'output_catalog_sha256': 'abc', 'records': [
            {'vehicle_id': 'a', 'ammo_id': 'a::one', 'reason': 'missing', 'status': 'unresolved'},
            {'vehicle_id': 'a', 'ammo_id': 'a::two', 'reason': 'other', 'status': 'unresolved'},
            {'vehicle_id': 'outside', 'ammo_id': 'outside::one', 'reason': 'missing', 'status': 'unresolved'}]}
        self.catalog = {'vehicles': [{'id': 'a', 'name': 'A'}, {'id': 'b', 'name': 'B', 'system_role': 'basic',
                        'multi_vehicle_system': {'capabilities_aggregated': False,
                                                 'members': [{'vehicle_id': 'launcher', 'role': 'component'}]}}]}

    def run_reconcile(self):
        return reconcile(self.gaps, self.summary, self.recovery, self.catalog)

    def test_records_are_grouped_without_inventing_reason_for_empty_vehicle(self):
        result = self.run_reconcile()
        self.assertEqual([v['vehicle_id'] for v in result['vehicles']], ['a', 'b'])
        self.assertEqual(result['vehicles_without_unresolved_records'], 1)
        self.assertEqual(result['vehicles'][1]['record_status'], 'no_unresolved_selection_record')
        self.assertEqual(result['vehicles'][1]['unresolved_selections'], [])
        self.assertFalse(result['vehicles'][1]['system_capabilities_aggregated'])
        self.assertEqual(result['vehicles'][1]['system_members'][0]['vehicle_id'], 'launcher')
        self.assertFalse(result['publishable'])

    def test_source_identity_and_summary_must_match(self):
        for obj, key, value in ((self.recovery, 'source_version', '2'),
                                (self.recovery, 'output_catalog_sha256', 'different'),
                                (self.summary, 'unresolved_selection_records', 1)):
            old = obj[key]; obj[key] = value
            with self.assertRaises(ValueError): self.run_reconcile()
            obj[key] = old

    def test_duplicate_or_missing_vehicle_and_record_are_rejected(self):
        self.gaps['ground_ammunition']['vehicles_without_any_exact_owned_profile'].append('a')
        with self.assertRaises(ValueError): self.run_reconcile()
        self.gaps['ground_ammunition']['vehicles_without_any_exact_owned_profile'].pop()
        self.recovery['records'].append(copy.deepcopy(self.recovery['records'][0]))
        with self.assertRaises(ValueError): self.run_reconcile()
        self.recovery['records'].pop(); self.catalog['vehicles'].pop()
        with self.assertRaises(ValueError): self.run_reconcile()


if __name__ == '__main__':
    unittest.main()
