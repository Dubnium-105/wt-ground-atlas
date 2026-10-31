import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from wt_atlas.research_maps import open_run, save_map
from wt_atlas.forecast.inventory import Inventory


class ResearchMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name) / 'maps'
        self.identity = {'target_id': 'v', 'target_sha256': 'geometry', 'model': 'test'}
        self.ammo = {'name': 'research', 'mode': 'KE', 'reference_only': True, 'distance_m': 100.,
                     'provenance': {'client_version': 'v1', 'datamine_commit': 'commit'}}
        self.plan = {'angles_deg': [0, 180], 'pixel_pitch_m': 1., 'world_width_m': 2., 'top_y_m': 2., 'bottom_y_m': 0.}
        self.actual = (np.full((2, 2, 2), .5, np.float32), np.ones((2, 2, 2), np.float32),
                       np.full((2, 2, 2), 3, np.uint8), np.zeros((2, 2, 2), np.uint8))

    def tearDown(self): self.temp.cleanup()

    def test_checkpoint_reuse_and_invalid_array_rebuild(self):
        first, reused = save_map(self.root, self.identity, self.ammo, self.actual, self.plan)
        self.assertFalse(reused); self.assertTrue(first['reference_only'])
        second, reused = save_map(self.root, self.identity, self.ammo, self.actual, self.plan)
        self.assertTrue(reused); self.assertEqual(first, second)
        (self.root / first['array_file']).write_bytes(b'corrupted')
        third, reused = save_map(self.root, self.identity, self.ammo, self.actual, self.plan)
        self.assertFalse(reused); self.assertEqual(third['array_sha256'], first['array_sha256'])

    def test_unmanaged_changed_identity_and_publishable_ammo_rejected(self):
        self.root.mkdir(); (self.root / 'existing').write_text('preserve')
        with self.assertRaises(ValueError): open_run(self.root, self.identity)
        self.assertEqual((self.root / 'existing').read_text(), 'preserve')
        other = Path(self.temp.name) / 'managed'; open_run(other, self.identity)
        with self.assertRaises(ValueError): open_run(other, {**self.identity, 'model': 'changed'})
        self.ammo['reference_only'] = False
        with self.assertRaises(ValueError): save_map(other, self.identity, self.ammo, self.actual, self.plan)

    def test_formal_inventory_rejects_research_map(self):
        save_map(self.root, self.identity, self.ammo, self.actual, self.plan)
        ammo_file = Path(self.temp.name) / 'ammo.json'; ammo_file.write_text(json.dumps([self.ammo]), encoding='utf8')
        catalog = {'vehicles': [{'id': 'v', 'nation': 'test', 'source_attributes': {'client': {'source_version': 'v1'}}}]}
        inventory = Inventory(Path(self.temp.name) / 'index.sqlite')
        try:
            inventory.sync(self.root, ammo_file, catalog)
            self.assertEqual(inventory.db.execute('select status, error, selected from maps').fetchall(),
                             [('rejected', 'unsupported_or_reference_ammunition', 0)])
        finally:
            inventory.close()


if __name__ == '__main__': unittest.main()
