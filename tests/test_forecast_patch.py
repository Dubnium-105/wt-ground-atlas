"""Version-event linkage regressions using JSON-separated weapon summaries."""
import copy
import json
import unittest
from test_forecast import settings
from wt_atlas.forecast.combat import weapon_timing
from wt_atlas.forecast.data import patch_catalog


class WeaponPatchTests(unittest.TestCase):
    def vehicle(self):
        return {"id": "fixture", "firepower": {"primary_weapon_id": "gun", "reload_type": "manual", "reload_ace_s": 6.},
                "ammo": [{"id": "round", "weapon_profile": {"id": "gun", "reload_type": "manual", "reload_ace_s": 6.}},
                         {"id": "rocket", "weapon_profile": {"id": "launcher", "reload_type": "autoloader", "autoload_s": 10.}}],
                "mobility": {}, "sensors": {}}

    def test_summary_reload_changes_actual_event_cadence(self):
        catalog = json.loads(json.dumps({"vehicles": [self.vehicle()]}))
        patch = {"entries": [{"vehicle_id": "fixture", "field": "firepower.reload_ace_s", "old": 6., "new": 3.}]}
        new, _ = patch_catalog(catalog, patch)
        v = new["vehicles"][0]; c = settings()
        timing = weapon_timing(v, v["ammo"][0], 0, c["scenarios"][0], c["timing"])
        self.assertEqual(timing["cycle_s"], 3.)
        self.assertEqual(catalog["vehicles"][0]["ammo"][0]["weapon_profile"]["reload_ace_s"], 6.)
        self.assertEqual(v["ammo"][1]["weapon_profile"]["autoload_s"], 10.)
        self.assertFalse(v.get("forecast_physics_invalidated", False))

    def test_unbound_summary_patch_cannot_silently_use_stale_weapon(self):
        v = self.vehicle(); del v["firepower"]["primary_weapon_id"]
        new, _ = patch_catalog({"vehicles": [v]}, {"entries": [{"vehicle_id": "fixture", "field": "firepower.reload_ace_s", "old": 6., "new": 3.}]})
        v = new["vehicles"][0]; c = settings()
        self.assertFalse(weapon_timing(v, v["ammo"][0], 0, c["scenarios"][0], c["timing"])["valid"])

    def test_conflicting_weapon_specific_old_value_is_rejected(self):
        v = self.vehicle(); v["ammo"][0]["weapon_profile"]["reload_ace_s"] = 8.
        with self.assertRaises(ValueError):
            patch_catalog({"vehicles": [v]}, {"entries": [{"vehicle_id": "fixture", "field": "firepower.reload_ace_s", "old": 6., "new": 3.}]})


if __name__ == "__main__": unittest.main()
