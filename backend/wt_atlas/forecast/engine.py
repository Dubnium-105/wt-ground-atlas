"""Variant enumeration, persistent shot cache and matched-room mission valuation."""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import MODEL_VERSION
from .aim import integrate, regions, skill_nodes
from .combat import duel, weapon_timing
from .data import Atlas, br_step, canonical, digest, file_hash, number, read_json, safe_file, taxonomy


def enumerate_variants(catalog: dict, atlas: Atlas) -> tuple[list[dict], list[dict]]:
    variants, rejected = [], []
    for vehicle in sorted(catalog["vehicles"], key=lambda v: v["id"]):
        domain = "ground" if vehicle["kind"] == "ground" else "air"
        block = vehicle.get("rating_configurations", {}).get("ground_rb")
        if block is not None:
            scenario_status = catalog.get("forecast_scenario", {}).get("status")
            permitted = block.get("status") == "official_verified" or (scenario_status in ("proposed", "announced", "hypothetical") and block.get("status") == scenario_status)
            if not permitted or not block.get("source", {}).get("url"):
                rejected.append({"vehicle_id": vehicle["id"], "reason": "unverified_BR_configuration"}); continue
            options = block.get("options", [])
            if not options or len({p["id"] for p in options}) != len(options): raise ValueError("Invalid BR option identities")
        else:
            options = [{"id": None, "br": {"current": vehicle.get("br", {}).get("current")}}]
        bindings = atlas.bindings(vehicle) if domain == "ground" else []
        for option in options:
            br = option.get("br", {}).get("current")
            if not number(br):
                rejected.append({"vehicle_id": vehicle["id"], "reason": "unknown_ground_rb"}); continue
            br_step(br)
            payloads = [(ammo, p, None) for ammo, p in bindings] if domain == "ground" else []
            if domain == "air":
                for loadout in vehicle.get("loadouts", []):
                    if loadout.get("legal") is not True: continue
                    weapons = loadout.get("weapons", [])
                    stations = [w["station"] for w in weapons if w.get("station") is not None]
                    weapon_ids = {w["id"] for w in weapons}
                    if len(stations) != len(set(stations)): continue
                    if any(not number(w.get("count")) or w["count"] <= 0 or
                           any(i in weapon_ids for i in w.get("conflicts_with", [])) or
                           any(i not in weapon_ids for i in w.get("requires", [])) for w in weapons): continue
                    mass_known = all(number(w.get("mass_kg")) for w in weapons)
                    capacity = loadout.get("max_payload_kg", vehicle.get("max_payload_kg"))
                    if mass_known and number(capacity) and sum(w["mass_kg"] * w["count"] for w in weapons) > capacity: continue
                    payloads.append((None, None, loadout))
            # Keep unknown configurations in the opponent population rather than erase them.
            if not payloads: payloads = [(None, None, None)]
            for ammo, profile, loadout in payloads:
                if block is not None:
                    has_policy = any(k in option for k in ("allowed_loadout_ids", "allowed_weapon_ids", "allowed_ammo_ids"))
                    if not has_policy: continue
                    if "allowed_loadout_ids" in option and (loadout is None or loadout["id"] not in option["allowed_loadout_ids"]): continue
                    if "allowed_weapon_ids" in option and (loadout is None or any(w["id"] not in option["allowed_weapon_ids"] for w in loadout["weapons"])): continue
                    if "allowed_ammo_ids" in option and (ammo is None or ammo["id"] not in option["allowed_ammo_ids"]): continue
                identity = {"vehicle_id": vehicle["id"], "rating_configuration_id": option["id"],
                            "ammo_id": ammo["id"] if ammo else None, "loadout_id": loadout["id"] if loadout else None}
                variants.append({**identity, "key": digest(identity), "vehicle": vehicle, "ammo": ammo,
                                 "profile": profile, "loadout": loadout, "nation": vehicle["nation"],
                                 "br": br, "br_step": br_step(br), "domain": domain,
                                 "classification": taxonomy(vehicle), "system_id": vehicle.get("system_id")})
    return sorted(variants, key=lambda v: v["key"]), rejected


class ShotCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS shots (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.hits, self.misses = 0, 0

    def get(self, key: str):
        record = self.db.execute("SELECT value FROM shots WHERE key=?", (key,)).fetchone()
        if record:
            import json
            self.hits += 1; return json.loads(record[0])
        self.misses += 1; return None

    def put(self, key: str, value: dict):
        self.db.execute("INSERT OR REPLACE INTO shots VALUES (?,?)", (key, canonical(value)))

    def close(self):
        self.db.commit(); self.db.close()


class Forecast:
    def __init__(self, catalog: dict, atlas: Atlas, config: dict, cache: ShotCache,
                 effects_path: Path | None = None, missions_path: Path | None = None):
        self.catalog, self.atlas, self.config, self.cache = catalog, atlas, config, cache
        self.variants, self.rejected = enumerate_variants(catalog, atlas)
        self.by_key = {v["key"]: v for v in self.variants}
        self.skills = skill_nodes(config["population"])
        self.enemy_skills = skill_nodes(config.get("opponent_population", config["population"]))
        self.aim_code_hash = file_hash(Path(__file__).with_name("aim.py"))
        self.effects_path, self.effects = effects_path, {}
        self.effect_array = (None, None)
        if effects_path:
            for row in read_json(effects_path)["records"]:
                if row["map_id"] in self.effects: raise ValueError("Duplicate effect kernel")
                self.effects[row["map_id"]] = row
        self.missions = {}
        if missions_path:
            for row in read_json(missions_path)["records"]:
                if row.get("status") != "validated" or not row.get("source") or row.get("source_version") != catalog.get("snapshot", {}).get("client_source", {}).get("version"):
                    raise ValueError("Unverified/external mission evidence version")
                if not all(number(row.get(k)) and 0 <= row[k] <= 1 for k in ("lower", "mean", "upper")) or not row["lower"] <= row["mean"] <= row["upper"]:
                    raise ValueError("Invalid mission evidence interval")
                key = (row["variant_key"], row["target_vehicle_id"], row["scenario_id"], row["task"])
                if key in self.missions: raise ValueError("Duplicate mission evidence")
                self.missions[key] = row
        self._features = (None, None)
        self.budget_exhaustions = 0

    def _effect(self, map_id: str, shape: tuple, angle_index: int):
        row = self.effects.get(map_id)
        if row is None: return None, None
        if row.get("status") not in ("assumed", "validated") or not row.get("source") or row.get("map_array_sha256") != self.atlas.meta[map_id]["array_sha256"]:
            raise ValueError("Effect kernel physical identity missing/mismatched")
        if "constant" in row:
            q = np.array(row["constant"], dtype=float)
            if q.shape != (3,): raise ValueError("Invalid effect vector")
            return np.broadcast_to(q, shape + (3,)), digest(row)
        if self.effect_array[0] != map_id:
            source = safe_file(self.effects_path.parent, row["array_file"])
            if file_hash(source) != row["array_sha256"]: raise ValueError("Effect array checksum")
            with np.load(source, allow_pickle=False) as data:
                array = data["conditional_outcomes"].copy()
            self.effect_array = (map_id, array)
        return self.effect_array[1][angle_index], digest(row)

    def shot(self, attacker: dict, target: dict, scenario: dict, skill: float) -> dict | None:
        if attacker["vehicle"].get("forecast_physics_invalidated") or target["vehicle"].get("forecast_physics_invalidated"):
            return None
        profile = attacker["profile"]
        if profile is None: return None
        map_id = self.atlas.map_for(target["vehicle_id"], profile["name"], scenario["physical_distance_m"])
        if map_id is None: return None
        m = self.atlas.meta[map_id]
        weapon = attacker["ammo"].get("weapon_profile") or attacker["vehicle"].get("firepower", {})
        gun = weapon.get("traverse_deg_s")
        stabilized = attacker["vehicle"].get("sensors", {}).get("stabilizer")
        stabilized = bool(stabilized) if stabilized is not None else None
        # No BR or lineup in a physical/aim cache key. All physical and aim identities are included.
        identity = {"map": m, "profile_sha256": digest(profile), "aim": self.config["aim"],
                    "code": self.aim_code_hash, "skill": skill, "gun": gun, "stabilized": stabilized,
                    "context": {k: scenario[k] for k in ("physical_distance_m", "aiming_distance_m", "yaw_deg",
                    "own_velocity_xy_m_s", "target_velocity_xy_m_s", "target_angular_velocity_xy_rad_s")},
                    "effect": self.effects.get(map_id), "endpoint": self.config.get("endpoint", "penetration"),
                    "effect_rewards": self.config.get("effect_rewards")}
        key = digest(identity)
        self.atlas.verify(map_id)  # A warm aim cache must not mask a corrupted source NPZ.
        cached = self.cache.get(key)
        if cached is not None: return cached
        arrays = self.atlas.load(map_id)
        angle = np.flatnonzero(arrays["angles_deg"] == scenario["yaw_deg"])
        if not len(angle): return None  # No nearest-view substitution.
        i = int(angle[0]); p, s = arrays["penetration_probability"][i], arrays["status"][i]
        pitch = float(arrays["pixel_pitch_m"])
        effects, effects_hash = self._effect(map_id, p.shape, i)
        feature_key = (map_id, i, digest(self.config["aim"]), effects_hash, self.config.get("endpoint", "penetration"))
        if self._features[0] != feature_key:
            reward = None
            if effects is not None and self.config.get("endpoint") == "damage":
                reward = p * (effects @ np.array(self.config["effect_rewards"]))
            self._features = (feature_key, regions(p, s, pitch, self.config["aim"], reward))
        result = integrate(p, s, pitch, skill, scenario, self.config["aim"], gun_control=gun,
                           stabilized=stabilized, effects=effects, prepared=self._features[1])
        result.update(map_id=map_id, target_id=target["vehicle_id"], ammo_profile_id=profile["name"],
                      catalog_ammo_id=attacker["ammo_id"], source_version=m["source_version"],
                      physical_distance_m=profile["distance_m"], yaw_deg=scenario["yaw_deg"],
                      source_binding=m["target_binding"], effect_sha256=effects_hash,
                      physical_map_sha256=m["array_sha256"], aim_cache_key=key,
                      effect_status=self.effects.get(map_id, {}).get("status", "not_supplied"))
        self.cache.put(key, result)
        return result

    @lru_cache(maxsize=50000)
    def pair(self, a_key: str, b_key: str, scenario_id: str) -> dict:
        a, b = self.by_key[a_key], self.by_key[b_key]
        scenario = next(s for s in self.config["scenarios"] if s["id"] == scenario_id)
        endpoint = self.config.get("endpoint", "penetration")
        totals = {"mean": 0., "lower": 0., "upper": 0., "coverage": 0., "first_fire": 0.}
        missing = set()
        for sa, wa in self.skills:
            for sb, wb in self.enemy_skills:
                weight = wa * wb
                pa, pb = self.shot(a, b, scenario, sa), self.shot(b, a, scenario, sb)
                ta = weapon_timing(a["vehicle"], a["ammo"] or {}, sa, scenario, self.config["timing"])
                tb = weapon_timing(b["vehicle"], b["ammo"] or {}, sb, scenario, self.config["timing"])
                if pa is None or pb is None or not ta["valid"] or not tb["valid"]:
                    missing.add("one_or_both_directions_or_timing_missing")
                    totals["mean"] += weight * .5; totals["upper"] += weight
                    continue
                missing.update(ta["missing"] + tb["missing"])
                if endpoint == "damage":
                    if pa["lethality"] is None or pb["lethality"] is None:
                        missing.add("post_penetration_kernel_missing")
                        totals["mean"] += weight * .5; totals["upper"] += weight; continue
                    qa, qb = pa["lethality"]["outcomes"], pb["lethality"]["outcomes"]
                    # Unknown joint damage outcomes cannot be reduced to independent scalar endpoints.
                    uncertain = max(pa["lethality"]["unknown_mass"], pb["lethality"]["unknown_mass"])
                    outcome = duel(ta, tb, qa, qb, scenario["horizon_s"], self.config["timing"], endpoint=endpoint)
                    lower, upper = (0., 1.) if uncertain > 1e-12 else (outcome["score"], outcome["score"])
                else:
                    outcome = duel(ta, tb, [0, 0, pa["mean"]], [0, 0, pb["mean"]], scenario["horizon_s"], self.config["timing"])
                    lower = duel(ta, tb, [0, 0, pa["lower"]], [0, 0, pb["upper"]], scenario["horizon_s"], self.config["timing"])["score"]
                    upper = duel(ta, tb, [0, 0, pa["upper"]], [0, 0, pb["lower"]], scenario["horizon_s"], self.config["timing"])["score"]
                totals["mean"] += weight * outcome["score"]
                totals["lower"] += weight * lower; totals["upper"] += weight * upper
                unknown_a = pa["lethality"]["unknown_mass"] if endpoint == "damage" else pa["unknown_mass"]
                unknown_b = pb["lethality"]["unknown_mass"] if endpoint == "damage" else pb["unknown_mass"]
                totals["coverage"] += weight * min(1 - unknown_a, 1 - unknown_b)
                totals["first_fire"] += weight * outcome["first_fire_probability"][0]
        return {**totals, "score": totals["mean"] if totals["coverage"] > 0 else None,
                "endpoint": endpoint, "missing": sorted(missing), "calibrated": False}

    def rooms(self, nation: str, cap: int) -> list[tuple[int, float]]:
        spread = self.config["matchmaking"]["spread_steps"]
        max_br = max((v["br_step"] for v in self.variants), default=cap)
        tops = list(range(cap, min(max_br, cap + spread) + 1))
        return [(top, 1 / len(tops)) for top in tops]

    @lru_cache(maxsize=1024)
    def opponents(self, nation: str, room_top: int, domain: str) -> tuple:
        rules = self.config["matchmaking"]
        rows = [v for v in self.variants if (rules["same_nation_enemies"] or v["nation"] != nation) and v["domain"] == domain]
        main = [v for v in rows if room_top - rules["spread_steps"] <= v["br_step"] <= room_top]
        carried = [v for v in rows if v["br_step"] < room_top - rules["spread_steps"]]
        def normalize(pool):
            groups = defaultdict(lambda: defaultdict(list))
            for v in pool:
                vehicle = v["vehicle"]
                group = vehicle.get("equivalence_group") if vehicle.get("equivalence_verified") is True else None
                groups[group or v["vehicle_id"]][v["vehicle_id"]].append(v)
            result = []
            for parents in groups.values():
                for variants in parents.values():
                    result += [(v["key"], 1 / len(groups) / len(parents) / len(variants)) for v in variants]
            return result
        a, b = normalize(main), normalize(carried)
        fraction = rules["lower_carried_mass"] if b else 0.
        if not a and b: fraction = 1.
        return tuple([(key, w * (1 - fraction)) for key, w in a] + [(key, w * fraction) for key, w in b])

    @lru_cache(maxsize=50000)
    def mission(self, key: str, task: str, room_top: int, scenario_id: str) -> dict:
        v = self.by_key[key]
        if task == "ground" and v["domain"] != "ground": return {"mean": 0., "lower": 0., "upper": 0., "coverage": 1., "usable": False}
        domain = "air" if task == "anti_air" else "ground"
        enemies = self.opponents(v["nation"], room_top, domain)
        totals = {"mean": 0., "lower": 0., "upper": 0., "coverage": 0., "usable": True}
        if not enemies: return {**totals, "mean": .5, "upper": 1.}
        for enemy, weight in enemies:
            if task == "ground":
                row = self.pair(key, enemy, scenario_id)
            else:
                external = self.missions.get((key, self.by_key[enemy]["vehicle_id"], scenario_id, task))
                row = {**external, "coverage": 1.} if external else {"mean": .5, "lower": 0., "upper": 1., "coverage": 0.}
            for name in ("mean", "lower", "upper", "coverage"):
                totals[name] += weight * row[name]
        return totals
