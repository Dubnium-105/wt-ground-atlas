"""Strict adapters for local V2 NPZ maps, catalog, ammunition and patch snapshots."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
from ..npz_io import load_npz_bounded


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"),
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Non-finite JSON: " + x)))


def number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def safe_file(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "%" in name:
        raise ValueError("Invalid relative file")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Path traversal")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink input is not supported: " + name)
    result = current.resolve()
    if not result.is_relative_to(root.resolve()) or not result.is_file():
        raise ValueError("Missing/outside input: " + name)
    return result


def verify_manifest(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.exists():
        return {"status": "not_supplied", "files": 0}
    manifest = read_json(path)
    seen = set()
    for item in manifest["files"]:
        name = item["path"]
        if name in seen:
            raise ValueError("Duplicate manifest path")
        seen.add(name)
        source = safe_file(root, name)
        if source.stat().st_size != item["bytes"] or file_hash(source) != item["sha256"]:
            raise ValueError("Manifest integrity failure: " + name)
    return {"status": "verified", "files": len(seen), "sha256": file_hash(path)}


def validate_sensitivity_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError('Sensitivity requires an explicit versioned sensitivity config section')
    try:
        thresholds = policy['thresholds']
        if policy['schema_version'] != '1.0.0' or policy['status'] != 'assumed_robustness_policy':
            raise ValueError('Unsupported sensitivity policy version/status')
        if not number(thresholds['minimum_overlap']) or not 0 < thresholds['minimum_overlap'] <= 1:
            raise ValueError('Invalid sensitivity overlap threshold')
        if not number(thresholds['maximum_score_delta']) or not 0 <= thresholds['maximum_score_delta'] <= 100:
            raise ValueError('Invalid sensitivity utility threshold')
        if not number(policy['aim_narrower_factor']) or not 0 < policy['aim_narrower_factor'] < 1:
            raise ValueError('Invalid sensitivity narrower factor')
        if not number(policy['aim_wider_factor']) or policy['aim_wider_factor'] <= 1:
            raise ValueError('Invalid sensitivity wider factor')
        if not number(policy['opponent_skill_mean_shift']) or policy['opponent_skill_mean_shift'] <= 0:
            raise ValueError('Invalid sensitivity opponent skill shift')
        for key in ('search_budget_multiplier', 'seed_offset'):
            if not isinstance(policy[key], int) or isinstance(policy[key], bool) or policy[key] < (2 if key == 'search_budget_multiplier' else 1):
                raise ValueError('Invalid sensitivity ' + key)
    except (KeyError, TypeError) as error:
        raise ValueError('Incomplete sensitivity policy') from error
    return policy


def validate_config(c: dict) -> None:
    for key in ("aim", "population", "timing", "matchmaking", "spawn", "search", "scenarios", "publication"):
        if key not in c:
            raise ValueError("Missing config section: " + key)
    # Reject booleans masquerading as numeric settings and NaN at any nesting level.
    def visit(x):
        if isinstance(x, dict):
            for v in x.values(): visit(v)
        elif isinstance(x, list):
            for v in x: visit(v)
        elif isinstance(x, float) and not math.isfinite(x):
            raise ValueError("Non-finite config")
    visit(c)
    for key, v in c["aim"].items():
        if not number(v) or v < 0:
            raise ValueError("Invalid aim parameter: " + key)
    for key in ("area_scale_m2", "depth_scale_m", "offset_scale_m", "sigma_x_m", "sigma_y_m",
                "distance_scale_m", "minimum_sigma_m", "control_reference_deg_s"):
        if c["aim"][key] <= 0: raise ValueError("Nonpositive aim scale: " + key)
    for key in ("weak_threshold", "unknown_prior", "stabilizer_residual"):
        if not 0 <= c["aim"][key] <= 1: raise ValueError("Invalid aim probability")
    if not isinstance(c["aim"]["max_regions"], int) or not 1 <= c["aim"]["max_regions"] <= 128:
        raise ValueError("Invalid region limit")
    p = c["population"]
    if p["distribution"] != "normal_assumption" or not number(p["mean"]) or not number(p["sd"]) or p["sd"] < 0:
        raise ValueError("Invalid population prior")
    if not isinstance(p["nodes"], int) or not 1 <= p["nodes"] <= 15: raise ValueError("Invalid quadrature size")
    for key, v in c["timing"].items():
        if not number(v) or v <= 0: raise ValueError("Invalid timing setting: " + key)
    if "sensitivity" in c:
        validate_sensitivity_policy(c["sensitivity"])
    search = c["search"]
    for key in ("beam_width", "exchange_width", "max_evaluations", "per_cap_limit", "global_limit"):
        if not isinstance(search[key], int) or isinstance(search[key], bool) or search[key] < 1:
            raise ValueError("Invalid search limit: " + key)
    if not isinstance(search["local_passes"], int) or search["local_passes"] < 0: raise ValueError("Invalid passes")
    if not search["slots"] or any(not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 10 for k in search["slots"]):
        raise ValueError("Slots must be integers in [1,10]")
    for key in ("risk_aversion", "minimum_pair_coverage", "diversity_overlap"):
        if not number(search[key]) or not 0 <= search[key] <= 1: raise ValueError("Invalid fraction: " + key)
    if not 0 < search["tail_mass"] <= 1: raise ValueError("Invalid CVaR mass")
    sp = c["spawn"]
    for key in ("initial_points", "ground_cost", "max_points", "max_states"):
        if not isinstance(sp[key], int) or isinstance(sp[key], bool) or sp[key] < 0: raise ValueError("Invalid spawn rule")
    if not sp["phases"] or len(sp["phases"]) != len(sp["phase_weights"]): raise ValueError("Invalid spawn phases")
    if any(not number(w) or w < 0 for w in sp["phase_weights"]) or not math.isclose(sum(sp["phase_weights"]), 1):
        raise ValueError("Phase weights must sum to one")
    gains = sp["point_gains"]
    if any(not isinstance(g["points"], int) or isinstance(g["points"], bool) or g["points"] < 0 or
           not number(g["probability"]) or not 0 <= g["probability"] <= 1 for g in gains):
        raise ValueError("Invalid spawn point transition")
    if not math.isclose(sum(g["probability"] for g in gains), 1): raise ValueError("Point-gain mass must equal one")
    if c.get("endpoint", "penetration") not in ("penetration", "damage"): raise ValueError("Unknown endpoint")
    if c.get("endpoint") == "damage":
        rewards = c.get("effect_rewards", [])
        if len(rewards) != 3 or any(not number(x) or not 0 <= x <= 1 for x in rewards): raise ValueError("Invalid effect rewards")
    rules = c["matchmaking"]
    if rules["mode"] != "ground_rb" or rules["br_step_denominator"] != 3: raise ValueError("Unsupported matchmaking rule grid/mode")
    if not isinstance(rules["spread_steps"], int) or isinstance(rules["spread_steps"], bool) or rules["spread_steps"] < 0: raise ValueError("Invalid BR spread")
    if not number(rules["lower_carried_mass"]) or not 0 <= rules["lower_carried_mass"] <= 1: raise ValueError("Invalid carried-vehicle prior")
    if not search["acquisition_profiles"] or any(p not in ("all", "researchable") for p in search["acquisition_profiles"]): raise ValueError("Invalid acquisition profiles")
    if sp["initial_points"] > sp["max_points"] or sp["max_states"] < 1: raise ValueError("Invalid spawn budget")
    if not isinstance(c["timing"]["max_events"], int) or isinstance(c["timing"]["max_events"], bool): raise ValueError("Invalid event limit")
    scenarios = c["scenarios"]
    if not scenarios or len({s["id"] for s in scenarios}) != len(scenarios): raise ValueError("Duplicate/empty scenarios")
    for s in scenarios:
        for key in ("weight", "physical_distance_m", "aiming_distance_m", "horizon_s", "approach_m", "cover_distance_m", "hull_offset_deg"):
            if not number(s[key]) or s[key] < 0: raise ValueError("Invalid scenario " + key)
        if s["horizon_s"] <= 0 or not number(s['yaw_deg']) or not 0 <= s["yaw_deg"] < 360: raise ValueError("Invalid scenario bounds")
        for key in ("target_velocity_xy_m_s", "own_velocity_xy_m_s", "target_angular_velocity_xy_rad_s"):
            if len(s[key]) != 2 or not all(number(v) for v in s[key]): raise ValueError("Invalid motion vector")
        if c["publication"]["require_consistent_distance"] and s["physical_distance_m"] != s["aiming_distance_m"]:
            raise ValueError("Ranking cannot substitute aiming distance for physical distance")
    if not math.isclose(sum(s["weight"] for s in scenarios), 1): raise ValueError("Scenario weights must sum to one")
    def positive_int(value):
        return isinstance(value,int) and not isinstance(value,bool) and value>0
    for profile in [*c.get('skill_profiles',{}).values(),c.get('opponent_population',p)]:
        if profile.get('distribution')!='normal_assumption' or not number(profile.get('mean')) or not number(profile.get('sd')) or profile['sd']<0 or not positive_int(profile.get('nodes')) or profile['nodes']>15:raise ValueError('Invalid skill profile')
    sampler=rules.get('lineup_sampler',{})
    if sampler and (not isinstance(sampler.get('enabled'),bool) or not positive_int(sampler.get('samples')) or not positive_int(sampler.get('slots')) or sampler['slots']>10 or not isinstance(sampler.get('seed'),int)):raise ValueError('Invalid lineup sampler')
    for profile in c.get('task_profiles',{}).values():
        weights=profile.get('phase_weights',[])
        if not weights or len(weights)!=len(profile.get('phases',[])) or any(not number(w) or w<0 for w in weights) or not math.isclose(sum(weights),1):raise ValueError('Invalid task profile')
    if any(not positive_int(v) for v in c['publication'].get('budgets',{}).values()):raise ValueError('Invalid publication budget')
    stats=c.get('statistics')
    if stats:
        if any(not positive_int(stats[k]) for k in ('minimum_samples','max_iterations','bins','bootstrap_draws')) or any(not number(stats[k]) or not 0<stats[k]<1 for k in ('train_fraction','validation_fraction')) or stats['train_fraction']+stats['validation_fraction']>=1 or not number(stats['shrinkage']) or stats['shrinkage']<0:raise ValueError('Invalid statistics settings')
    if search.get('br_range') and (len(search['br_range'])!=2 or any(not number(x) for x in search['br_range']) or search['br_range'][0]>search['br_range'][1]):raise ValueError('Invalid BR range')


def taxonomy(v: dict) -> dict:
    role = v.get("wiki_role") or v.get("role")
    if v["kind"] == "ground":
        cls = {"mbt": "medium_tank", "medium": "medium_tank", "light": "light_tank", "ifv": "light_tank",
               "heavy": "heavy_tank", "td": "tank_destroyer", "anti_air": "spaa"}.get(role, role)
        if cls not in ("light_tank", "medium_tank", "heavy_tank", "tank_destroyer", "spaa"): cls = "unknown"
        return {"domain": "ground", "platform": "ground_vehicle", "class": cls,
                "subclass": "mbt" if v.get("role") == "mbt" else None}
    if v["kind"] == "helicopter":
        cls = {"utility_helicopter": "utility", "attack_helicopter": "attack", "attack_heli": "attack"}.get(role, "unknown")
        return {"domain": "air", "platform": "helicopter", "class": cls}
    cls = {"assault": "attacker", "cas": "attacker", "jet_fighter": "fighter", "interceptor": "fighter",
           "dive_bomber": "bomber", "frontline_bomber": "bomber", "naval_fighter": "fighter"}.get(role, role)
    return {"domain": "air", "platform": "fixed_wing", "class": cls if cls in ("fighter", "attacker", "bomber") else "unknown"}


def br_step(value: float, denominator: int = 3) -> int:
    if not number(value) or value < 0: raise ValueError("Invalid BR")
    step = round(value * denominator)
    if abs(round(step / denominator, 1) - value) > 1e-7: raise ValueError("Unsupported BR grid")
    return step


def patch_catalog(catalog: dict, patch: dict | None) -> tuple[dict, list[dict]]:
    result = copy.deepcopy(catalog)
    if patch is None: return result, []
    by_id = {v["id"]: v for v in result["vehicles"]}
    seen, audit = set(), []
    for row in patch.get("entries", []):
        identity = (row.get("vehicle_id"), row.get("field"), row.get("configuration_id"))
        if identity in seen: raise ValueError("Duplicate patch target")
        seen.add(identity)
        v = by_id.get(row.get("vehicle_id"))
        if v is None: raise ValueError("Unmapped patch vehicle")
        field = row.get("field")
        if field in ("br", "br.current", "br.planned"):
            if v.get("rating_configurations", {}).get("ground_rb"):
                raise ValueError("Configuration-specific BR patch required")
            old = v.get("br", {}).get("current")
            if old != row["old"]: raise ValueError("Patch old-value conflict")
            br_step(row["new"])
            v["br"]["current"] = row["new"]
        elif field == "rating_configuration_br":
            block = v.get("rating_configurations", {}).get("ground_rb", {})
            option = next((c for c in block.get("options", []) if c["id"] == row.get("configuration_id")), None)
            if option is None or option["br"]["current"] != row["old"]: raise ValueError("Patch configuration conflict")
            br_step(row["new"]); option["br"]["current"] = row["new"]
        elif isinstance(field, str) and field.split(".")[0] in ("firepower", "mobility", "sensors", "ammo", "loadouts", "protection", "rating_configurations"):
            parts = field.split(".")
            if any(not part or part.startswith("_") or part in ("constructor", "prototype") for part in parts):
                raise ValueError("Unsafe patch path")
            destination = v
            for part in parts[:-1]:
                if not isinstance(destination.get(part), dict): raise ValueError("Patch path is not an object")
                destination = destination[part]
            if canonical(destination.get(parts[-1])) != canonical(row["old"]): raise ValueError("Patch old-value conflict")
            if parts[0] == "firepower":
                # Catalog summaries and per-ammunition weapon profiles are distinct
                # JSON objects. A summary-only edit must not leave event timing stale.
                primary = v.get("firepower", {}).get("primary_weapon_id")
                profiles = [a["weapon_profile"] for a in v.get("ammo", []) if a.get("weapon_profile")]
                matching = [w for w in profiles if primary and w.get("id") == primary]
                if len(parts) != 2 or not matching or parts[-1] == "primary_weapon_id":
                    if profiles: v["forecast_timing_unverified"] = True
                else:
                    if any(canonical(w.get(parts[-1])) != canonical(row["old"]) for w in matching):
                        raise ValueError("Weapon-specific patch old-value conflict")
                    for w in matching: w[parts[-1]] = copy.deepcopy(row["new"])
            destination[parts[-1]] = copy.deepcopy(row["new"])
            if parts[0] in ("ammo", "protection"):
                v["forecast_physics_invalidated"] = True
        else:
            raise ValueError("Unsupported patch field; never silently ignore: " + str(field))
        audit.append({**row, "applied": True})
    result["forecast_scenario"] = {"id": patch.get("id"), "status": patch.get("status", "unknown"), "sha256": digest(patch)}
    return result, audit


class Atlas:
    """Index small metadata, load exactly one map on demand. No reference-slice fallback."""
    def __init__(self, root: Path, ammunition: Path, catalog: dict, *, index=None, memory_budget=512 * 1024**2, profile_history=None):
        from .profile_history import ProfileHistory
        history=profile_history or ProfileHistory()
        self.root = root.resolve()
        self.memory_budget = memory_budget
        raw = read_json(ammunition)
        if not isinstance(raw, list): raise ValueError("Ammunition must be an array")
        self.profiles = {a["name"]: a for a in raw}
        if len(self.profiles) != len(raw): raise ValueError("Duplicate ammunition identity")
        for v in catalog["vehicles"]:
            nation = v.get("nation")
            if not isinstance(nation, str) or not nation or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in nation):
                raise ValueError("Unsafe nation/shard identity")
        self.catalog = {v["id"]: v for v in catalog["vehicles"]}
        if len(self.catalog) != len(catalog["vehicles"]): raise ValueError("Duplicate catalog vehicle")
        self._binding_index = {}
        for profile in self.profiles.values():
            for ammo_id in profile.get("provenance", {}).get("penetration_source_ammo_ids", []):
                self._binding_index.setdefault(ammo_id, []).append(profile)
        self.meta = {}
        self.lookup = {}
        for path in ([] if index is not None else sorted((self.root / "arrays").glob("*.json"))):
            item = read_json(path)
            for key in ("map_id", "target_id", "target_sha256", "ammo_id", "ammo_profile_sha256",
                        "source_version", "datamine_commit", "array_file", "array_sha256"):
                if not item.get(key): raise ValueError("Missing map identity: " + key)
            profile = self.profiles.get(item["ammo_id"])
            if profile is None:
                raise ValueError("Exact ammunition profile hash mismatch")
            proof=history.reconcile(item['ammo_profile_sha256'],profile)
            if proof:item['profile_reconciliation']=proof
            else:item.pop('profile_reconciliation',None)
            provenance = profile.get("provenance", {})
            if provenance.get("client_version") != item["source_version"] or provenance.get("datamine_commit") != item["datamine_commit"]:
                raise ValueError("Map/profile version mismatch")
            if profile.get("mode") not in ("KE", "CE") or item.get("reference_only") or profile.get("reference_only"):
                raise ValueError("Unsupported/reference-only ammunition")
            if not number(profile.get("distance_m")) or profile["distance_m"] < 0: raise ValueError("Unknown map physical distance")
            key = (item["target_id"], item["ammo_id"], profile["distance_m"])
            if key in self.lookup or item["map_id"] in self.meta: raise ValueError("Ambiguous physical map identity")
            target = self.catalog.get(item["target_id"])
            if target:
                client = target.get("client_collision") or target.get("source_attributes", {}).get("client", {})
                if client.get("source_version") != item["source_version"]: raise ValueError("Catalog/map target version mismatch")
                expected = target.get("analysis_target_sha256")
                if expected and expected != item["target_sha256"]: raise ValueError("Catalog/map target hash mismatch")
                item["target_binding"] = "catalog_hash_verified" if expected else "source_declared_id_version_only"
            else:
                item["target_binding"] = "catalog_target_missing"
            item["metadata_sha256"] = file_hash(path)
            item.pop("images", None)  # PNG paths never enter physics or offline memory indices.
            self.meta[item["map_id"]] = item
            self.lookup[key] = item["map_id"]
        if index is not None:
            self.meta, self.lookup = index.metadata, index.lookup
        elif not self.meta: raise ValueError("No V2 map metadata under atlas/arrays")
        self._loaded_id = None
        self._loaded = None
        self.loads = 0
        self._verified = {}

    def verify(self, map_id: str) -> Path:
        m = self.meta[map_id]
        source = safe_file(self.root, m["array_file"])
        stat = source.stat()
        stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if self._verified.get(map_id) != stamp:
            if file_hash(source) != m["array_sha256"]: raise ValueError("NPZ checksum failure")
            self._verified[map_id] = stamp
        return source

    def map_for(self, target_id: str, ammo_id: str, distance: float) -> str | None:
        return self.lookup.get((target_id, ammo_id, distance))

    def load(self, map_id: str) -> dict:
        source = self.verify(map_id)
        if self._loaded_id == map_id: return self._loaded
        m = self.meta[map_id]
        data = load_npz_bounded(source, ("penetration_probability", "status", "material_response_flags",
                    "angles_deg", "pixel_pitch_m", "top_y_m", "bottom_y_m", "world_width_m"), self.memory_budget)
        p, s, f = (data[k] for k in ("penetration_probability", "status", "material_response_flags"))
        if p.dtype != np.float32 or s.dtype != np.uint8 or f.dtype != np.uint8 or p.ndim != 3 or p.shape != s.shape or p.shape != f.shape:
            raise ValueError("Invalid NPZ array layout/dtypes")
        angles = data["angles_deg"]
        if angles.ndim != 1 or len(angles) != p.shape[0] or len(set(angles.tolist())) != len(angles) or not np.all(np.isfinite(angles)) or np.any((angles < 0) | (angles >= 360)):
            raise ValueError("Invalid angle array")
        if not np.all(np.isin(s, [0, 1, 2, 3])) or np.any(f > 15): raise ValueError("Invalid status/flags")
        if np.any(~np.isfinite(p[s == 3])) or np.any((p[np.isfinite(p)] < 0) | (p[np.isfinite(p)] > 1)):
            raise ValueError("Invalid conditional probabilities")
        pitch = float(data["pixel_pitch_m"])
        if not math.isfinite(pitch) or pitch <= 0: raise ValueError("Invalid pixel pitch")
        if any(np.asarray(data[k]).ndim != 0 or not np.isfinite(data[k]) for k in ("pixel_pitch_m", "top_y_m", "bottom_y_m", "world_width_m")):
            raise ValueError("Invalid scalar map coordinates")
        if not math.isclose(float(data["world_width_m"]), p.shape[2] * pitch, abs_tol=1e-6) or not math.isclose(float(data["top_y_m"] - data["bottom_y_m"]), p.shape[1] * pitch, abs_tol=1e-6):
            raise ValueError("Map coordinate bounds mismatch")
        if "status_counts" in m and any(int(np.count_nonzero(s == int(k))) != count for k, count in m["status_counts"].items()):
            raise ValueError("Status count audit mismatch")
        if m.get("angles_deg") is not None and angles.tolist() != m["angles_deg"]:
            raise ValueError("Exact angle metadata mismatch")
        self._loaded_id, self._loaded = map_id, data
        self.loads += 1
        return data

    def bindings(self, vehicle: dict) -> list[tuple[dict, dict]]:
        result = []
        for ammo in vehicle.get("ammo", []):
            for p in self._binding_index.get(ammo["id"], []):
                if ammo["id"] in p.get("provenance", {}).get("penetration_source_ammo_ids", []):
                    client = vehicle.get("source_attributes", {}).get("client", {})
                    if client.get("source_version") != p["provenance"].get("client_version"): continue
                    if p.get("mode") not in ("KE", "CE") or p.get("reference_only"): continue
                    result.append((ammo, p))
        return result
