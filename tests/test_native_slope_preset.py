import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wt_atlas.forecast import native_slope_preset as subject


class NativeSlopePresetTests(unittest.TestCase):
    def binding(self):
        return {'unit_path': 'gamedata/units/tankmodels/ussr_t_60_1941.blk',
                'weapon_path': 'gamedata/weapons/groundmodels_weapons/20mm_tnsh_user_cannon.blk',
                'selector': '20mm_ussr_TNSh_AP',
                'bullets': [{'bulletName': '20mm_shvak_apit', 'bulletType': 'ap_i', 'ricochetPreset': 'defaultExt'}]}

    def test_source_identity_and_field_presence_are_exact(self):
        binding = self.binding()
        self.assertFalse(subject.projectile_evidence(binding)['slope_effect_preset_present'])
        for key, value in [('unit_path', 'different'), ('weapon_path', 'different'), ('selector', 'different')]:
            altered = copy.deepcopy(binding); altered[key] = value
            with self.assertRaises(ValueError): subject.projectile_evidence(altered)
        for change in ({'slopeEffectPreset': 'ap'}, {'slopeEffectPreset': None}, {'bulletName': 'different'}):
            altered = copy.deepcopy(binding); altered['bullets'][0].update(change)
            with self.assertRaises(ValueError): subject.projectile_evidence(altered)
        altered = copy.deepcopy(binding); altered['bullets'].append(copy.deepcopy(altered['bullets'][0]))
        with self.assertRaises(ValueError): subject.projectile_evidence(altered)

    def test_reviewed_executable_and_each_instruction_range_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / 'client.exe'; raw = b'0123456789'; executable.write_bytes(raw)
            ranges = {'example': (2, 4, hashlib.sha256(raw[2:6]).hexdigest())}
            with patch.object(subject, 'EXE_SIZE', len(raw)), patch.object(subject, 'EXE_SHA256', hashlib.sha256(raw).hexdigest()), patch.object(subject, 'RANGES', ranges):
                self.assertEqual(subject.verify_instruction_ranges(executable)['example']['offset'], 2)
                ranges['example'] = (2, 4, '0' * 64)
                with self.assertRaisesRegex(ValueError, 'native range'): subject.verify_instruction_ranges(executable)
                ranges['example'] = (2, 4, hashlib.sha256(raw[2:6]).hexdigest())
                executable.write_bytes(b'abcdefghij')
                with self.assertRaisesRegex(ValueError, 'executable'): subject.verify_instruction_ranges(executable)


if __name__ == '__main__': unittest.main()
