from pathlib import Path
import tempfile
import unittest

from wt_atlas.forecast.native_bullet_count import observed_selectors, verify_instruction_ranges


class NativeBulletCountTests(unittest.TestCase):
    def test_preserves_distinct_missing_and_explicit_zero_in_source_order(self):
        unit={'modifications':{'allowed':{}}}
        weapon={'allowed':{'bullet':[{'bulletName':'a'}, {'bulletName':'b','bulletCount':0},
                                      {'bulletName':'c','bulletCount':3}]},
                'undeclared':{'bullet':{'bulletName':'d'}}}
        rows=observed_selectors(unit,weapon)
        self.assertEqual([x['name'] for x in rows[0]['source_projectile_blocks']],['a','b','c'])
        self.assertEqual([x['bulletCount_present'] for x in rows[0]['source_projectile_blocks']],[False,True,True])
        self.assertEqual([x['bulletCount'] for x in rows[0]['source_projectile_blocks']],[None,0,3])
        self.assertEqual(len(rows),1)

    def test_unreviewed_executable_cannot_produce_native_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'aces.exe';path.write_bytes(b'fake')
            with self.assertRaisesRegex(ValueError,'Unreviewed client'):verify_instruction_ranges(path)


if __name__ == '__main__':unittest.main()
