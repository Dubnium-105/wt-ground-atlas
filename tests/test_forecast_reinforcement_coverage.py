import unittest
from wt_atlas.forecast.reinforcement_coverage import compare


class ReinforcementCoverageTests(unittest.TestCase):
    def fixture(self):
        evidence={'source_version':'v1','configured_candidates':{'country_a':{
            'tank':{'candidates':['tank_a','CASE']},'spaa':{'candidates':['aa_a','missing']}}}}
        catalog={'vehicles':[{'id':'tank_a'},{'id':'case'},{'id':'aa_a'}]}
        gaps={'source_version':'v1','vehicles':[{'vehicle_id':'aa_a','record_status':'unresolved'}]}
        return evidence,catalog,gaps

    def test_exact_gaps_and_unverified_case_only_suggestion_are_separate(self):
        result=compare(*self.fixture())
        self.assertEqual(result['catalog_match_counts'],{'case_only':1,'exact':2,'unmatched':1})
        self.assertEqual(result['exact_profile_gaps_by_group'],{'spaa':1})
        case=next(row for row in result['candidates'] if row['configured_id']=='CASE')
        self.assertEqual(case['case_only_suggestions'],['case'])
        self.assertFalse(case['in_exact_profile_gap_inventory'])
        self.assertFalse(result['publishable'])

    def test_inconsistent_evidence_is_rejected(self):
        e,c,g=self.fixture();g['source_version']='other'
        with self.assertRaisesRegex(ValueError,'versions differ'):compare(e,c,g)
        e,c,g=self.fixture();g['vehicles'].append({'vehicle_id':'not_in_catalog','record_status':'unresolved'})
        with self.assertRaisesRegex(ValueError,'outside the catalog'):compare(e,c,g)
        e,c,g=self.fixture();e['configured_candidates']['country_a']['spaa']['candidates'].append('tank_a')
        with self.assertRaisesRegex(ValueError,'Repeated'):compare(e,c,g)


if __name__ == '__main__':unittest.main()
