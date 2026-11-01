import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from wt_atlas.kinetic_missile_points import DISTANCES
from wt_atlas.kinetic_research_audit import summarize_checks


class KineticResearchAuditTests(unittest.TestCase):
    def setUp(self):
        self.report = {'status': 'passed', 'publishable': False, 'game_probability_validated': False,
                       'source_probability_maps_read': 0, 'checks': [
            {'distance_m': d, 'sampled_rays': 10, 'computed_rays': 5, 'partial_probability_rays': 2,
             'max_probability_delta': .000001, 'research_map': {'reference_only': True, 'map_id': 'a'*64,
                                                                  'array_sha256': 'b'*64}} for d in DISTANCES]}

    def test_summarizes_real_shape_without_claiming_game_validation(self):
        result = summarize_checks(self.report)
        self.assertEqual((result['sampled_rays'], result['partial_probability_rays'], result['checks_with_partial_probability']), (40, 8, 4))

    def test_rejects_promoted_incomplete_or_nonparity_receipts(self):
        for edit in ('promoted', 'missing', 'error', 'counts', 'hash'):
            r = copy.deepcopy(self.report)
            if edit == 'promoted': r['publishable'] = True
            if edit == 'missing': r['checks'].pop()
            if edit == 'error': r['checks'][0]['max_probability_delta'] = .1
            if edit == 'counts': r['checks'][0]['partial_probability_rays'] = 6
            if edit == 'hash': r['checks'][0]['research_map']['array_sha256'] = 'bad'
            with self.assertRaises(ValueError): summarize_checks(r)


if __name__ == '__main__': unittest.main()
