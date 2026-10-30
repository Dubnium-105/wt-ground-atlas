"""Behavioral acceptance tests for the complete V3 path; fixture facts are fictitious."""
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from wt_atlas.forecast.aim import integrate, regions, skill_nodes
from wt_atlas.forecast.combat import duel, weapon_timing
from wt_atlas.forecast.data import Atlas, canonical, digest, file_hash, patch_catalog, safe_file, taxonomy, validate_config
from wt_atlas.forecast.engine import Forecast, ShotCache, enumerate_variants
from wt_atlas.forecast.search import Optimizer, feasible, lower_tail, sortie_policy


def settings():
    c = json.loads((ROOT / "config/forecast.json").read_text())
    c["population"]["sd"] = 0
    c["scenarios"] = [copy.deepcopy(c["scenarios"][0])]
    c["scenarios"][0].update(weight=1., horizon_s=8.)
    c["search"].update(slots=[2], beam_width=64, max_evaluations=1000, local_passes=1)
    c["spawn"].update(phases=["ground", "ground"], phase_weights=[.6, .4])
    return c


def fixture(root: Path):
    atlas_root = root / "v2"; (atlas_root / "arrays").mkdir(parents=True)
    vehicles = []
    profiles = []
    for nation in ("a", "b"):
        for i in range(2):
            identifier = f"synthetic_{nation}_{i}"
            weapon = {"reload_type": "manual", "reload_ace_s": 2. + i, "traverse_deg_s": 30., "total_ammunition": 10}
            ammo = {"id": identifier + "::round", "weapon_profile": weapon}
            vehicle = {"id": identifier, "name": identifier, "nation": nation, "kind": "ground", "role": "mbt",
                       "br": {"current": 9.3, "planned": 9.3}, "special": False, "ammo": [ammo], "firepower": weapon,
                       "mobility": {"forward_kmh": 50., "reverse_kmh": 20., "power_to_weight_hp_t": 20.},
                       "sensors": {"thermal_generation": 1, "stabilizer": True}, "loadouts": [],
                       "analysis_target_sha256": hashlib.sha256(identifier.encode()).hexdigest(),
                       "source_attributes": {"client": {"source_version": "synthetic-only"}}}
            vehicles.append(vehicle)
            profiles.append({"name": identifier + "_profile", "mode": "KE", "distance_m": 10.,
                             "provenance": {"client_version": "synthetic-only", "datamine_commit": "synthetic-commit",
                                            "penetration_source_ammo_ids": [ammo["id"]]}})
    catalog = {"sample_subset": False, "test_fixture": True, "snapshot": {"id": "synthetic-only", "client_source": {"version": "synthetic-only"}}, "vehicles": vehicles}
    (root / "catalog.json").write_text(canonical(catalog)); (root / "ammo.json").write_text(canonical(profiles))
    for target in vehicles:
        for profile in profiles:
            map_id = digest([target["id"], profile["name"]])
            p = np.full((1, 20, 20), .55, np.float32)
            s = np.full(p.shape, 3, np.uint8)
            path = atlas_root / "arrays" / (map_id + ".npz")
            np.savez_compressed(path, penetration_probability=p, status=s, material_response_flags=np.zeros_like(s),
                                angles_deg=np.array([0.]), pixel_pitch_m=np.array(.1), top_y_m=np.array(2.), bottom_y_m=np.array(0.), world_width_m=np.array(2.))
            meta = {"map_id": map_id, "target_id": target["id"], "target_sha256": target["analysis_target_sha256"],
                    "ammo_id": profile["name"], "ammo_profile_sha256": digest(profile), "source_version": "synthetic-only",
                    "datamine_commit": "synthetic-commit", "array_file": "arrays/" + path.name, "array_sha256": file_hash(path)}
            path.with_suffix(".json").write_text(canonical(meta))
    return catalog, atlas_root, root / "ammo.json"


class AimTests(unittest.TestCase):
    def setUp(self):
        self.c = settings(); self.s = self.c["scenarios"][0]
        self.p = np.full((30, 40), .8, np.float32); self.status = np.full(self.p.shape, 3, np.uint8)

    def test_normal_quadrature_moments(self):
        nodes = skill_nodes({"distribution": "normal_assumption", "mean": 0., "sd": 1., "nodes": 5})
        self.assertAlmostEqual(sum(w for _, w in nodes), 1.)
        self.assertAlmostEqual(sum(x * w for x, w in nodes), 0.)
        self.assertAlmostEqual(sum(x * x * w for x, w in nodes), 1.)

    def test_impact_mass_and_distinct_unknown_semantics(self):
        p = np.full((4, 4), np.nan, np.float32)
        status = np.array([[0, 0, 0, 0], [1, 2, 3, 3], [1, 3, 3, 0], [0, 0, 0, 0]], np.uint8)
        p[status == 3] = .5
        r = integrate(p, status, .02, 0, self.s, self.c["aim"])
        self.assertAlmostEqual(sum(r[k] for k in ("known_mass", "unknown_mass", "no_protected_target_mass", "miss_mass")), 1.)
        self.assertGreater(r["unknown_mass"], 0); self.assertGreater(r["no_protected_target_mass"], 0)
        self.assertGreater(r["miss_mass"], 0); self.assertIsNone(r["lethality"])
        self.assertLess(r["lower"], r["upper"])

    def test_softmax_concentration_changes_between_weakpoints(self):
        p = np.zeros((30, 40), np.float32); p[3:10, 3:10] = .7; p[15:27, 20:35] = .95
        lo = integrate(p, self.status, .1, -1, self.s, self.c["aim"])
        hi = integrate(p, self.status, .1, 1, self.s, self.c["aim"])
        self.assertLess(hi["aim_points"][0]["weight"], lo["aim_points"][0]["weight"])
        self.assertGreater(hi["aim_points"][1]["weight"] / hi["aim_points"][2]["weight"], lo["aim_points"][1]["weight"] / lo["aim_points"][2]["weight"])

    def test_motion_anisotropy_and_skill(self):
        base = integrate(self.p, self.status, .1, 0, self.s, self.c["aim"])
        moving = integrate(self.p, self.status, .1, 0, {**self.s, "target_velocity_xy_m_s": [10., 0.]}, self.c["aim"])
        skilled = integrate(self.p, self.status, .1, 1, self.s, self.c["aim"])
        self.assertGreater(moving["sigma_xy_m"][0], base["sigma_xy_m"][0])
        self.assertEqual(moving["sigma_xy_m"][1], base["sigma_xy_m"][1])
        self.assertLess(skilled["sigma_xy_m"][0], base["sigma_xy_m"][0])

    def test_ring_center_is_inside_and_all_weak_canvas_finite(self):
        p = np.ones((7, 7), np.float32); p[2:5, 2:5] = 0
        _, weak = regions(p, np.full(p.shape, 3, np.uint8), .1, self.c["aim"])
        y, x = weak[0]["center_yx"]; self.assertEqual(p[y, x], 1)
        r = integrate(self.p, self.status, .1, 0, self.s, self.c["aim"])
        self.assertTrue(math.isfinite(r["mean"]))

    def test_effect_kernel_differentiates_equal_penetration(self):
        a = integrate(self.p, self.status, .1, 0, self.s, self.c["aim"], effects=np.broadcast_to([0., 0., .8], self.p.shape + (3,)))
        b = integrate(self.p, self.status, .1, 0, self.s, self.c["aim"], effects=np.broadcast_to([0., 0., .1], self.p.shape + (3,)))
        self.assertEqual(a["mean"], b["mean"])
        self.assertGreater(a["lethality"]["outcomes"][2], b["lethality"]["outcomes"][2])
        with self.assertRaises(ValueError):
            integrate(self.p, self.status, .1, 0, self.s, self.c["aim"], effects=np.ones(self.p.shape + (3,)))

    def test_reject_nan_known_and_unknown_status(self):
        p = self.p.copy(); p[0, 0] = np.nan
        with self.assertRaises(ValueError): integrate(p, self.status, .1, 0, self.s, self.c["aim"])
        s = self.status.copy(); s[0, 0] = 8
        with self.assertRaises(ValueError): integrate(self.p, s, .1, 0, self.s, self.c["aim"])


class CombatTests(unittest.TestCase):
    def setUp(self):
        self.c = settings()["timing"]
        self.t = {"valid": True, "first_s": 0., "cycle_s": 1., "ready_capacity": 8, "exposure_end_s": 8.}

    def test_simultaneous_symmetry_and_mass(self):
        a = duel(self.t, self.t, [0, 0, .7], [0, 0, .2], 5, self.c)
        b = duel(self.t, self.t, [0, 0, .2], [0, 0, .7], 5, self.c)
        self.assertAlmostEqual(a["score"] + b["score"], 1.)
        self.assertAlmostEqual(np.sum(a["state_mass"]), 1.)
        self.assertEqual(a["first_fire_probability"], [.5, .5]); self.assertIsNone(a["kill_probability"])

    def test_reload_changes_events_not_final_weight(self):
        slow = duel(self.t, self.t, [0, 0, .2], [0, 0, .2], 5, self.c)
        fast = duel({**self.t, "cycle_s": .1}, self.t, [0, 0, .2], [0, 0, .2], 5, self.c)
        self.assertGreater(fast["score"], slow["score"])
        early = duel(self.t, {**self.t, "first_s": 2.}, [0, 0, .5], [0, 0, .5], 5, self.c)
        self.assertGreater(early["score"], .5)

    def test_fire_disabled_stops_followups(self):
        r = duel(self.t, {**self.t, "first_s": .5}, [1., 0., 0.], [0., 0., 1.], 5, self.c, endpoint="damage")
        self.assertEqual(r["expected_shots"][1], 0.)
        self.assertEqual(r["kill_probability"], 0.); self.assertEqual(r["score"], 1.)

    def test_belt_limit_and_budget(self):
        r = duel({**self.t, "ready_capacity": 1}, self.t, [0, 0, 0], [0, 0, 0], 5, self.c)
        self.assertEqual(r["expected_shots"][0], 1.)
        with self.assertRaises(ValueError): duel({**self.t, "cycle_s": .00001, "ready_capacity": None}, self.t, [0, 0, .2], [0, 0, .2], 5, self.c)

    def test_automatic_uses_rpm_and_missile_interval_not_human_reload(self):
        v = {"firepower": {}, "mobility": {}, "sensors": {}}
        s = settings()["scenarios"][0]
        r = weapon_timing(v, {"weapon_profile": {"reload_type": "automatic", "fire_rate_rpm": 600}}, 0, s, self.c)
        self.assertEqual(r["cycle_s"], .1)
        m = weapon_timing(v, {"weapon_profile": {"reload_type": "mechanism_unspecified", "listed_reload_s": .1}}, 0, s, self.c)
        self.assertFalse(m["valid"])


class PolicyTests(unittest.TestCase):
    def test_cvar_splits_boundary_mass(self):
        self.assertAlmostEqual(lower_tail([(0., .05), (.5, .15), (1., .8)], .1), .25)

    def test_sp_unlocks_later_sortie_and_unknown_air_not_free(self):
        c = settings()["spawn"]; c.update(initial_points=150, ground_cost=150, phase_weights=[.5, .5], point_gains=[{"points": 300, "probability": 1.}])
        item = lambda x: {"mean": x, "lower": x, "upper": x, "coverage": 1., "usable": True}
        rows = [[item(.6), item(0.)], [item(.1), item(1.)]]
        r = sortie_policy(rows, [150, 300], c)
        unavailable = sortie_policy(rows, [150, None], c)
        self.assertAlmostEqual(r["mean"], .8); self.assertGreater(r["mean"], unavailable["mean"])
        self.assertEqual(r["first_action"], 0)
        self.assertEqual(unavailable["coverage"], 1.)
        with self.assertRaises(ValueError): sortie_policy(rows, [-1, 300], c)

    def test_bounds_follow_selected_policy(self):
        c = settings()["spawn"]
        rows = [[{"mean": .6, "lower": .2, "upper": .8, "coverage": .5}, {"mean": .3, "lower": .3, "upper": .3, "coverage": 1.}]] * 2
        r = sortie_policy(rows, [150, 150], c)
        self.assertLessEqual(r["lower"], r["mean"]); self.assertGreaterEqual(r["upper"], r["mean"])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.cat, self.aroot, self.ammo = fixture(self.root)
        self.atlas = Atlas(self.aroot, self.ammo, self.cat)
        self.cache = ShotCache(self.root / "cache.sqlite")
        self.config = settings()
        self.engine = Forecast(self.cat, self.atlas, self.config, self.cache)

    def tearDown(self): self.cache.close(); self.tmp.cleanup()

    def test_source_identity_and_native_range_only(self):
        a = next(v for v in self.engine.variants if v["nation"] == "a")
        b = next(v for v in self.engine.variants if v["nation"] == "b")
        s = self.config["scenarios"][0]
        self.assertIsNotNone(self.engine.shot(a, b, s, 0))
        self.assertIsNone(self.engine.shot(a, b, {**s, "physical_distance_m": 1000.}, 0))
        self.assertIsNone(self.engine.shot(a, b, {**s, "yaw_deg": 15.}, 0))
        corrupt = copy.deepcopy(self.cat); corrupt["vehicles"][0]["source_attributes"]["client"]["source_version"] = "wrong"
        with self.assertRaises(ValueError): Atlas(self.aroot, self.ammo, corrupt)

    def test_profile_hash_and_ownership(self):
        profiles = json.loads(self.ammo.read_text()); profiles[0]["distance_m"] = 100.
        self.ammo.write_text(canonical(profiles))
        with self.assertRaises(ValueError): Atlas(self.aroot, self.ammo, self.cat)
        v = copy.deepcopy(self.cat["vehicles"][0]); v["ammo"][0]["id"] = "foreign"
        self.assertEqual(self.atlas.bindings(v), [])

    def test_missing_reverse_never_invented(self):
        a = next(v for v in self.engine.variants if v["nation"] == "a")
        b = next(v for v in self.engine.variants if v["nation"] == "b")
        self.atlas.lookup.pop((a["vehicle_id"], b["profile"]["name"], 10.))
        result = self.engine.pair(a["key"], b["key"], self.config["scenarios"][0]["id"])
        self.assertIsNone(result["score"]); self.assertEqual(result["coverage"], 0.)

    def test_duplicate_variant_does_not_multiply_parent_prior(self):
        v = next(v for v in self.engine.variants if v["nation"] == "b")
        first = self.engine.opponents("a", 28, "ground")
        mass = sum(w for k, w in first if self.engine.by_key[k]["vehicle_id"] == v["vehicle_id"])
        extra = {**v, "key": "extra-config"}; self.engine.variants.append(extra); self.engine.by_key[extra["key"]] = extra
        self.engine.opponents.cache_clear()
        later = self.engine.opponents("a", 28, "ground")
        self.assertAlmostEqual(mass, sum(w for k, w in later if self.engine.by_key[k]["vehicle_id"] == v["vehicle_id"]))
        self.assertAlmostEqual(sum(w for _, w in later), 1.)

    def test_beam_matches_exhaustive_small_fixture(self):
        opt = Optimizer(self.engine)
        result = opt.discover("a", 28, 2, "all")
        pool = [v for v in self.engine.variants if v["nation"] == "a"]
        exhaustive = [opt.evaluate(tuple(sorted(v["key"] for v in line))) for n in (1, 2) for line in itertools.combinations(pool, n) if feasible(line, 2)]
        self.assertAlmostEqual(result["candidates"][0]["score"], max(r["score"] for r in exhaustive if r["score"] is not None))
        self.assertFalse(result["global_optimum_guaranteed"])
        bad = (pool[0], {**pool[0], "key": "another"})
        self.assertFalse(feasible(bad, 2))

    def test_patch_isolation_and_physics_invalidation(self):
        v = self.cat["vehicles"][0]
        patched, audit = patch_catalog(self.cat, {"entries": [{"vehicle_id": v["id"], "field": "br", "old": 9.3, "new": 10.3}]})
        self.assertEqual(v["br"]["current"], 9.3); self.assertEqual(patched["vehicles"][0]["br"]["current"], 10.3)
        self.assertFalse(patched["vehicles"][0].get("forecast_physics_invalidated", False))
        damaged, _ = patch_catalog(self.cat, {"entries": [{"vehicle_id": v["id"], "field": "ammo", "old": v["ammo"], "new": v["ammo"]}]})
        self.assertTrue(damaged["vehicles"][0]["forecast_physics_invalidated"])
        with self.assertRaises(ValueError): patch_catalog(self.cat, {"entries": [{"vehicle_id": v["id"], "field": "br", "old": 1., "new": 10.3}]})

    def test_configuration_enumeration_and_allowed_ammo(self):
        catalog = copy.deepcopy(self.cat); v = catalog["vehicles"][0]
        v["rating_configurations"] = {"ground_rb": {"status": "official_verified", "source": {"url": "fixture"}, "options": [
            {"id": "low", "br": {"current": 9.3}, "allowed_ammo_ids": []},
            {"id": "high", "br": {"current": 10.3}, "allowed_ammo_ids": [v["ammo"][0]["id"]]}]}}
        variants, _ = enumerate_variants(catalog, self.atlas)
        ours = [a for a in variants if a["vehicle_id"] == v["id"]]
        self.assertEqual(len(ours), 1); self.assertEqual(ours[0]["rating_configuration_id"], "high")
        self.assertEqual(ours[0]["br"], 10.3)

    def test_taxonomy_and_no_heli_guess(self):
        self.assertEqual(taxonomy(self.cat["vehicles"][0])["class"], "medium_tank")
        self.assertEqual(taxonomy({"kind": "helicopter", "role": "cas"})["class"], "unknown")

    def test_config_and_path_safety(self):
        validate_config(self.config)
        invalid = copy.deepcopy(self.config); invalid["scenarios"][0]["aiming_distance_m"] = 1000.
        with self.assertRaises(ValueError): validate_config(invalid)
        with self.assertRaises(ValueError): safe_file(self.root, "../outside")
        with self.assertRaises(ValueError): safe_file(self.root, "/etc/passwd")

    def test_cached_shot_does_not_hide_corrupt_npz(self):
        a = next(v for v in self.engine.variants if v["nation"] == "a")
        b = next(v for v in self.engine.variants if v["nation"] == "b")
        scenario = self.config["scenarios"][0]
        result = self.engine.shot(a, b, scenario, 0)
        meta = self.atlas.meta[result["map_id"]]
        path = self.aroot / meta["array_file"]
        path.write_bytes(b"corrupt" + path.read_bytes())
        with self.assertRaises(ValueError): self.engine.shot(a, b, scenario, 0)

    def test_no_reference_slice_fallback(self):
        empty = self.root / "reference_slice"; empty.mkdir()
        with self.assertRaises(ValueError): Atlas(empty, self.ammo, self.cat)

    def test_damage_endpoint_requires_explicit_kernels(self):
        self.config["endpoint"] = "damage"
        a = next(v for v in self.engine.variants if v["nation"] == "a")
        b = next(v for v in self.engine.variants if v["nation"] == "b")
        missing = self.engine.pair(a["key"], b["key"], self.config["scenarios"][0]["id"])
        self.assertIsNone(missing["score"])
        rows = [{"map_id": k, "map_array_sha256": m["array_sha256"], "status": "assumed", "source": "synthetic-test", "constant": [.2, .1, .1]} for k, m in self.atlas.meta.items()]
        path = self.root / "effects.json"; path.write_text(canonical({"records": rows}))
        engine = Forecast(self.cat, self.atlas, self.config, self.cache, path)
        result = engine.pair(a["key"], b["key"], self.config["scenarios"][0]["id"])
        self.assertIsNotNone(result["score"]); self.assertEqual(result["endpoint"], "damage")

    def test_budget_exhaustion_is_disclosed(self):
        self.config["search"]["max_evaluations"] = 1
        result = Optimizer(self.engine).discover("a", 28, 2, "all")
        self.assertTrue(result["budget_exhausted"]); self.assertLessEqual(result["evaluations"], 1)

    def test_nested_patch_and_duplicate_rejected(self):
        vehicle = self.cat["vehicles"][0]
        row = {"vehicle_id": vehicle["id"], "field": "firepower.reload_ace_s", "old": 2.0, "new": 1.0}
        after, _ = patch_catalog(self.cat, {"entries": [row]})
        self.assertEqual(after["vehicles"][0]["firepower"]["reload_ace_s"], 1.)
        self.assertEqual(vehicle["firepower"]["reload_ace_s"], 2.)
        with self.assertRaises(ValueError): patch_catalog(self.cat, {"entries": [row, row]})

    def test_br_only_change_reuses_aim_cache(self):
        a = next(v for v in self.engine.variants if v["nation"] == "a")
        b = next(v for v in self.engine.variants if v["nation"] == "b")
        scenario = self.config["scenarios"][0]
        first = self.engine.shot(a, b, scenario, 0)
        modified = copy.deepcopy(self.cat); modified["vehicles"][0]["br"]["current"] = 10.3
        other = Forecast(modified, self.atlas, self.config, self.cache)
        hits = self.cache.hits
        second = other.shot(other.by_key[a["key"]], other.by_key[b["key"]], scenario, 0)
        self.assertEqual(first["aim_cache_key"], second["aim_cache_key"]); self.assertGreater(self.cache.hits, hits)

    def test_calibration_matches_disjoint_and_reports_without_auto_validation(self):
        from wt_atlas.forecast.calibrate import fit
        c = copy.deepcopy(self.config)
        c["calibration"]["bounds"] = {"sigma_x_m": [.01, 1.], "sigma_y_m": [.01, 1.]}
        c["calibration"]["max_iterations"] = 20
        a = next(v for v in self.engine.variants if v["nation"] == "a")
        b = next(v for v in self.engine.variants if v["nation"] == "b")
        map_id = self.atlas.map_for(b["vehicle_id"], a["profile"]["name"], 10.)
        rng = np.random.default_rng(123)
        rows = [{"match_id": str(i // 10), "map_id": map_id, "attacker_id": a["vehicle_id"], "catalog_ammo_id": a["ammo_id"],
                 "skill": 0., "x_pixel": float(9 + rng.normal(0, .25)), "y_pixel": float(9 + rng.normal(0, .25)),
                 "scenario": self.config["scenarios"][0]} for i in range(80)]
        observations = {"source": "synthetic-only", "client_version": "synthetic-only", "mode": "ground_rb", "skill_source": "synthetic-fixed", "time_window": "synthetic", "shots": rows}
        result = fit(self.cat, self.atlas, c, observations)
        self.assertEqual(result["report"]["train_matches"], 6)
        self.assertEqual(result["report"]["validation_matches"], 2)
        self.assertFalse(result["report"]["automatically_accepted"])
        self.assertLess(result["report"]["fitted_validation_negative_log_likelihood"], result["report"]["baseline_validation_negative_log_likelihood"])
        self.assertNotEqual(result["config"]["calibration_status"], "validated")

    def test_cli_full_generation_and_reproducible_cache(self):
        config = self.root / "config.json"; config.write_text(canonical(self.config))
        patch = self.root / "patch.json"; patch.write_text(canonical({"id": "test", "status": "proposed", "entries": [{"vehicle_id": self.cat["vehicles"][0]["id"], "field": "br", "old": 9.3, "new": 9.7}]}))
        out = self.root / "site"
        args = [sys.executable, str(ROOT / "scripts/build_forecast.py"), "--catalog", str(self.root / "catalog.json"), "--ammunition", str(self.ammo), "--atlas", str(self.aroot), "--config", str(config), "--scenario", str(patch), "--out", str(out), "--cache", str(self.root / "other.sqlite")]
        first = subprocess.run(args, check=True, text=True, capture_output=True)
        self.assertIn('"planned"', first.stdout)
        before = {p.relative_to(out).as_posix(): file_hash(p) for p in out.rglob("*") if p.is_file()}
        second = subprocess.run(args, check=True, text=True, capture_output=True)
        after = {p.relative_to(out).as_posix(): file_hash(p) for p in out.rglob("*") if p.is_file()}
        self.assertEqual(before, after); self.assertGreater(json.loads(second.stdout)["cache_hits"], 0)
        index = json.loads((out / "index.json").read_text())
        self.assertEqual(set(index["worlds"]), {"current", "planned"})
        self.assertFalse(any(p.suffix in (".npz", ".sqlite") for p in out.rglob("*")))
        for item in list(index["worlds"].values()) + [index["report"], index["migrations"]]:
            self.assertEqual(file_hash(out / item["url"]), item["sha256"])


if __name__ == "__main__": unittest.main()
