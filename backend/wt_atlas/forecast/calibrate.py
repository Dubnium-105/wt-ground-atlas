"""Fit aim parameters to observed impact coordinates with match-disjoint validation.

No aggregate K/D-to-skill conversion, no renormalization onto the visible target.
This command needs observations; the physics sample alone is not training data.
"""
from __future__ import annotations

import argparse
import copy
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

from .aim import integrate, regions
from .data import Atlas, canonical, digest, number, read_json, validate_config


def fit(catalog: dict, atlas: Atlas, config: dict, observations: dict) -> dict:
    cfg = config["calibration"]
    for key in ("source", "client_version", "mode", "skill_source", "time_window"):
        if not observations.get(key): raise ValueError("Missing observation provenance: " + key)
    if observations["mode"] != "ground_rb": raise ValueError("Wrong observation mode")
    rows = observations.get("shots", [])
    groups = sorted({r["match_id"] for r in rows}, key=lambda x: digest(str(x)))
    if len(groups) < cfg["minimum_matches"] or len(rows) < cfg["minimum_shots"]:
        raise ValueError("Insufficient independent calibration observations")
    holdout_count = max(1, min(len(groups)-1, round(len(groups) * cfg["holdout_fraction"])))
    heldout = set(groups[:holdout_count])
    by_id = {v["id"]: v for v in catalog["vehicles"]}
    prepared = []
    for row in rows:
        m = atlas.meta.get(row["map_id"])
        if m is None or m["source_version"] != observations["client_version"]: raise ValueError("Observation map/version mismatch")
        attacker = by_id.get(row["attacker_id"])
        if attacker is None: raise ValueError("Unknown observation attacker")
        binding = next(((a, p) for a, p in atlas.bindings(attacker) if a["id"] == row["catalog_ammo_id"] and p["name"] == m["ammo_id"]), None)
        if binding is None: raise ValueError("Observation does not prove ammunition ownership")
        if not all(number(row[k]) for k in ("skill", "x_pixel", "y_pixel")): raise ValueError("Invalid observed impact")
        scenario = row["scenario"]
        if scenario["physical_distance_m"] != binding[1]["distance_m"]: raise ValueError("Observation physical distance mismatch")
        arrays = atlas.load(row["map_id"])
        angle = np.flatnonzero(arrays["angles_deg"] == scenario["yaw_deg"])
        if not len(angle): raise ValueError("Unsupported observation view")
        index = int(angle[0]); p = arrays["penetration_probability"][index].copy(); s = arrays["status"][index].copy()
        pitch = float(arrays["pixel_pitch_m"])
        gun = (binding[0].get("weapon_profile") or attacker.get("firepower", {})).get("traverse_deg_s")
        stable = attacker.get("sensors", {}).get("stabilizer")
        prepared.append((row, p, s, pitch, gun, bool(stable) if stable is not None else None, regions(p, s, pitch, config["aim"])))
    names = list(cfg["bounds"])
    allowed = {"sigma_x_m", "sigma_y_m", "skill_error", "temperature", "center_skill"}
    if not names or not set(names) <= allowed: raise ValueError("Unsupported calibration parameter")
    bounds = [cfg["bounds"][name] for name in names]
    if any(len(b) != 2 or not all(number(x) for x in b) or b[0] <= 0 or b[1] <= b[0] for b in bounds): raise ValueError("Invalid fit bounds")
    initial = np.array([config["aim"][name] for name in names])

    def loss(x, validation=False):
        aim = {**config["aim"], **dict(zip(names, x))}
        total, count = 0., 0
        for row, p, s, pitch, gun, stable, features in prepared:
            if (row["match_id"] in heldout) != validation: continue
            result = integrate(p, s, pitch, row["skill"], row["scenario"], aim, gun_control=gun, stabilized=stable, prepared=features)
            sx, sy = result["sigma_xy_m"]
            terms = []
            for point in result["aim_points"]:
                if point["weight"] <= 0: continue
                cy, cx = point["center_yx"]
                dx, dy = (row["x_pixel"] - cx) * pitch, (row["y_pixel"] - cy) * pitch
                terms.append(math.log(point["weight"]) - math.log(2 * math.pi * sx * sy) - .5 * ((dx / sx) ** 2 + (dy / sy) ** 2))
            total -= float(logsumexp(terms)); count += 1
        if not count: raise ValueError("Empty train/validation group")
        return total / count
    baseline_validation = loss(initial, True)
    result = minimize(loss, initial, bounds=bounds, method="L-BFGS-B", options={"maxiter": cfg["max_iterations"]})
    fitted = copy.deepcopy(config)
    fitted["aim"].update({name: float(value) for name, value in zip(names, result.x)})
    validate_config(fitted)
    fitted["calibration_status"] = "fitted_requires_independent_review"
    fitted["model_version"] = config["model_version"] + "+fit-" + digest(observations)[:12]
    return {"config": fitted, "report": {"source": observations["source"], "observations_sha256": digest(observations),
            "skill_source": observations["skill_source"], "client_version": observations["client_version"],
            "mode": observations["mode"], "time_window": observations["time_window"],
            "shots": len(rows), "train_matches": len(groups)-holdout_count, "validation_matches": holdout_count,
            "split": "deterministic_match_disjoint", "optimizer_success": bool(result.success),
            "optimizer_message": str(result.message), "train_negative_log_likelihood": float(result.fun),
            "baseline_validation_negative_log_likelihood": baseline_validation,
            "fitted_validation_negative_log_likelihood": loss(result.x, True),
            "empirical_win_rate_validated": False, "automatically_accepted": False}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("catalog", "atlas", "ammunition", "config", "observations", "out"):
        p.add_argument("--" + name, required=True)
    args = p.parse_args()
    catalog = read_json(Path(args.catalog)); config = read_json(Path(args.config)); validate_config(config)
    atlas = Atlas(Path(args.atlas), Path(args.ammunition), catalog)
    result = fit(catalog, atlas, config, read_json(Path(args.observations)))
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / "fitted-config.json").write_text(canonical(result["config"]), encoding="utf-8")
    (out / "fit-report.json").write_text(canonical(result["report"]), encoding="utf-8")


if __name__ == "__main__": main()
