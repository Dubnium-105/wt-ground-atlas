"""Area-aware weakpoint choice and untruncated, anisotropic impact integration."""
from __future__ import annotations

import math
import numpy as np
from scipy.ndimage import distance_transform_edt, label
from scipy.special import expit, ndtr, roots_hermitenorm, softmax


def skill_nodes(prior: dict) -> list[tuple[float, float]]:
    """Gauss-Hermite quadrature for the declared normal prior, not measured players."""
    if prior["sd"] == 0:
        return [(float(prior["mean"]), 1.0)]
    nodes, weights = roots_hermitenorm(prior["nodes"])
    return [(float(prior["mean"] + prior["sd"] * x), float(w / math.sqrt(2 * math.pi)))
            for x, w in zip(nodes, weights)]


def regions(probability: np.ndarray, status: np.ndarray, pitch: float, c: dict,
            reward: np.ndarray | None = None) -> tuple[tuple[int, int], list[dict]]:
    if probability.ndim != 2 or probability.shape != status.shape:
        raise ValueError("Expected matching two-dimensional grids")
    valid = np.argwhere(status != 0)
    if not len(valid): return (0, 0), []
    centroid = valid.mean(axis=0)
    center = tuple(int(x) for x in valid[np.argmin(np.sum((valid - centroid) ** 2, axis=1))])
    values = probability if reward is None else reward
    if values.shape != probability.shape: raise ValueError("Reward map shape mismatch")
    weak = (status == 3) & np.isfinite(values) & (values >= c["weak_threshold"])
    labels, count = label(weak)  # Four-neighbour connectivity, never across diagonals.
    output = []
    for i in range(1, count + 1):
        mask = labels == i
        points = np.argwhere(mask)
        # Padding makes an all-weak canvas still have a finite external boundary.
        depth = distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1]
        candidates = np.argwhere(depth == depth.max())
        mid = points.mean(axis=0)
        y, x = candidates[np.argmin(np.sum((candidates - mid) ** 2, axis=1))]
        area = len(points) * pitch * pitch
        inset = float(depth[y, x] * pitch)
        offset = float(np.linalg.norm(np.array([y, x]) - centroid) * pitch)
        value = float(values[mask].mean())
        utility = (c["value_weight"] * value + c["area_weight"] * math.log1p(area / c["area_scale_m2"])
                   + c["depth_weight"] * math.log1p(inset / c["depth_scale_m"])
                   - c["eccentricity_weight"] * offset / c["offset_scale_m"])
        output.append({"center_yx": [int(y), int(x)], "area_m2": area, "inset_m": inset,
                       "offset_m": offset, "value": value, "utility": utility})
    output.sort(key=lambda r: (-r["utility"], r["center_yx"]))
    return center, output[:c["max_regions"]]


def integrate(probability: np.ndarray, status: np.ndarray, pitch: float, skill: float,
              scenario: dict, c: dict, *, gun_control: float | None = None,
              stabilized: bool | None = None, effects: np.ndarray | None = None,
              prepared: tuple | None = None) -> dict:
    """Return penetration expectation and, only when provided, post-penetration outcomes.

    effects[...,0:3] are conditional [fire_disabled, immobilized, destroyed].
    Their mutually exclusive sum must be <= 1; residual is no effect. No kernel
    means no lethality claim. Status 1 is zero ONLY for the protected-target event.
    """
    if not math.isfinite(skill) or not math.isfinite(pitch) or pitch <= 0: raise ValueError("Invalid aim inputs")
    p, s = np.asarray(probability), np.asarray(status)
    if p.ndim != 2 or p.shape != s.shape or not np.all(np.isin(s, [0, 1, 2, 3])): raise ValueError("Invalid aim grid")
    if np.any(~np.isfinite(p[s == 3])) or np.any((p[np.isfinite(p)] < 0) | (p[np.isfinite(p)] > 1)):
        raise ValueError("Invalid known probabilities")
    if effects is not None:
        effects = np.asarray(effects)
        if effects.shape != p.shape + (3,) or not np.all(np.isfinite(effects)) or np.any(effects < 0) or np.any(effects.sum(axis=-1) > 1 + 1e-12):
            raise ValueError("Invalid conditional damage kernel")
    center, weak = prepared if prepared is not None else regions(p, s, pitch, c)
    center_weight = float(expit(c["center_intercept"] - c["center_skill"] * skill)) if weak else 1.0
    temperature = c["temperature"] * math.exp(np.clip(c["temperature_skill"] * skill, -30, 30))
    choices = softmax(temperature * np.array([r["utility"] for r in weak])) if weak else np.array([])
    points = [{"center_yx": list(center), "weight": center_weight, "type": "center"}]
    points += [{**r, "weight": float((1 - center_weight) * weight), "type": "weakpoint"}
               for r, weight in zip(weak, choices)]
    d = scenario["aiming_distance_m"]
    scale = max(c["distance_floor"], d / c["distance_scale_m"]) * math.exp(-c["skill_error"] * skill)
    control = 1.0 if gun_control is None else math.exp(c["control_sensitivity"] *
                (c["control_reference_deg_s"] - gun_control) / c["control_reference_deg_s"])
    residual = c["stabilizer_residual"] if stabilized is True else 1.0
    own = np.array(scenario["own_velocity_xy_m_s"]) * c["motion_time_s"] * residual
    target = np.array(scenario["target_velocity_xy_m_s"]) * c["motion_time_s"]
    angular = np.array(scenario["target_angular_velocity_xy_rad_s"]) * d * c["angular_time_s"]
    base = np.array([c["sigma_x_m"], c["sigma_y_m"]]) * scale * control
    sigma = np.maximum(c["minimum_sigma_m"], np.sqrt(base * base + own * own + target * target + angular * angular))
    height, width = p.shape
    mass = np.zeros(p.shape, dtype=np.float64)
    for point in points:
        y, x = point["center_yx"]
        # CDF differences integrate whole pixel rectangles, including miss regions.
        px = np.diff(ndtr((np.arange(width + 1) - x - 0.5) * pitch / sigma[0]))
        py = np.diff(ndtr((np.arange(height + 1) - y - 0.5) * pitch / sigma[1]))
        mass += point["weight"] * np.outer(py, px)
    if not np.any(s != 0):
        mass.fill(0)
    known = float(mass[s == 3].sum())
    unknown = float(mass[s == 2].sum())
    no_target = float(mass[s == 1].sum())
    miss = float(mass[s == 0].sum()) + max(0.0, 1.0 - float(mass.sum()))
    lower = float(np.sum(mass[s == 3] * p[s == 3]))
    upper = min(1.0, lower + unknown)
    result = {"mean": lower + c["unknown_prior"] * unknown, "lower": lower, "upper": upper,
              "known_mass": known, "unknown_mass": unknown, "no_protected_target_mass": no_target,
              "miss_mass": miss, "sigma_xy_m": sigma.tolist(), "aim_points": points,
              "skill": skill, "semantics": "protected_target_penetration", "lethality": None}
    if effects is not None:
        q = (mass[s == 3] * p[s == 3]) @ effects[s == 3]
        result["lethality"] = {"outcomes": q.tolist(), "unknown_mass": min(1.0, unknown + no_target),
                               "status": "conditional_supplied_kernel", "no_effect": float(1 - q.sum()),
                               "note": "Unknown/no-protected-target mass is not proof of no vehicle damage."}
    # Do not hide numerical mass-loss by renormalizing onto successful pixels.
    if not math.isclose(known + unknown + no_target + miss, 1.0, abs_tol=1e-10):
        raise ArithmeticError("Impact mass not conserved")
    return result
