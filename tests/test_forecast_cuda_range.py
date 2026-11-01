from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.cuda_range import range_kernel


class CudaRangeTemplateTests(unittest.TestCase):
    def test_range_adapter_matches_pinned_kernel_and_rejects_changed_template(self):
        # CuPy is only imported by PreparedVehicle.__init__, not by CODE.
        sys.path.insert(0,str(ROOT/'backend/vendor/grp_probability_v2/lib'))
        from wt_armor.layered_penetration_v2 import CODE as code
        derived=range_kernel(code)
        self.assertIn('__fsub_rn(entry[i],first)',derived)
        self.assertEqual(derived.count('target,distance_curve,curve_count,impact_distance)'),3)
        with self.assertRaisesRegex(ValueError,'template'):
            range_kernel(code.replace('float shift=0.0f;','float shift = 0.0f;'))


if __name__=='__main__':unittest.main()
