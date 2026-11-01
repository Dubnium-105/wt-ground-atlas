import json
from pathlib import Path
import tempfile
import unittest

from wt_atlas.forecast.random_reinforcements import audit,client_claims,official_claims,requires_unmodeled_gate,configured_ground_candidates
from wt_atlas.native_binding.ordered_blk import parse_ordered_blk


class RandomReinforcementEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.catalog={'vehicles':[{'id':'ground','kind':'ground','br':{'current':9.3}}]}

    def test_gate_covers_threshold_and_dynamic_options_without_inventing_other_versions(self):
        self.assertTrue(requires_unmodeled_gate(self.catalog,'2.59.0.92','full_ground_rb'))
        self.catalog['vehicles'][0]['br']['current']=9.0
        self.assertFalse(requires_unmodeled_gate(self.catalog,'2.59.0.92','full_ground_rb'))
        self.catalog['vehicles'][0]['rating_configurations']={'ground_rb':{'options':[{'br':{'current':9.3}}]}}
        self.assertTrue(requires_unmodeled_gate(self.catalog,'2.59.0.92','full_ground_rb'))
        self.assertFalse(requires_unmodeled_gate(self.catalog,'2.59.0.91','full_ground_rb'))
        self.assertFalse(requires_unmodeled_gate(self.catalog,'2.59.0.92','ground_research'))
        self.catalog['vehicles'][0]['system_role']='component'
        self.assertFalse(requires_unmodeled_gate(self.catalog,'2.59.0.92','full_ground_rb'))

    def test_sources_must_contain_the_documented_claims_and_session_state(self):
        official=b'<html><body>9.3 or higher; one random tank and one random SPAA; maximum BR for the session; if there is no vehicle in the research tree</body></html>'
        self.assertEqual(official_claims(official)['historical_ground_rb_groups'],['tank','spaa'])
        with self.assertRaises(ValueError):official_claims(official.replace(b'9.3',b'9.0'))
        script=(b'get_user_custom_state(userIdInt64.get(), false)\nthis.getMyStateBlk()?.random_units\n'
                b'function getRandomUnitsList(groupName)\nfunction isUnitEnabledByRandomGroups(unitName)')
        self.assertFalse(client_claims(script)['selection_probabilities_verified'])
        with self.assertRaises(ValueError):client_claims(script.replace(b'random_units',b'other'))

    def test_unreviewed_client_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'capture.json').write_text(json.dumps({'packs':{'gui.vromfs.bin':{'version':'wrong','sha256':'bad'}}}))
            with self.assertRaisesRegex(ValueError,'Unreviewed'):audit(p/'capture.json',p/'official.html')

    def test_configured_candidates_preserve_groups_and_reject_ambiguous_blocks(self):
        source = '''RandomSpawnUnits {
  tank_random_battles_historical_base {
    country_test {
      tank {
        minPlayerMRank:i = 25
        tank_a {
        }
        tank_b {
        }
      }
      spaa {
        spaa_a {
        }
      }
    }
  }
}
'''
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'gameplay.blk'; path.write_text(source,encoding='utf8')
            groups=configured_ground_candidates(parse_ordered_blk(path))['country_test']
            self.assertEqual(groups['tank']['candidates'],['tank_a','tank_b'])
            self.assertEqual(groups['tank']['parameters']['minPlayerMRank'],{'type':'i','value':25})
            self.assertEqual(groups['spaa']['candidates'],['spaa_a'])
            path.write_text(source.replace('tank_b {','tank_a {'),encoding='utf8')
            with self.assertRaisesRegex(ValueError,'Duplicated'):configured_ground_candidates(parse_ordered_blk(path))
            path.write_text(source.replace('tank_b {','tank_b {\n          forceWeapon:t = "x"'),encoding='utf8')
            with self.assertRaisesRegex(ValueError,'Unreviewed'):configured_ground_candidates(parse_ordered_blk(path))


if __name__ == '__main__':unittest.main()
