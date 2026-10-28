#!/usr/bin/env python3
"""Portable entry point for the accepted V2 GRP probability algorithm."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time

PACKAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE / "lib"))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_id(value):
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise ValueError("An ammunition/vehicle name must be a nonempty file-safe ID")
    if any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in value):
        raise ValueError("Use ASCII letters, digits, '_' or '-' in IDs: " + value)
    if value.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        raise ValueError("Reserved file name: " + value)
    return value


def finite_number(value, label, minimum=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + " must be a number")
    if not math.isfinite(value) or value < minimum or abs(value) > 3.4e38:
        raise ValueError("Invalid " + label)
    return float(value)


def validate_table(rows, label, probability=False):
    if not isinstance(rows, list):
        raise ValueError(label + " must be an array")
    previous = -math.inf
    for ratio, points in rows:
        ratio = finite_number(ratio, label + " ratio")
        if ratio <= previous or not points:
            raise ValueError(label + " ratios must increase and point lists must be nonempty")
        previous = ratio
        last_x = -math.inf
        for x, y in points:
            x = finite_number(x, label + " cosine", -1.0)
            y = finite_number(y, label + " value")
            if x <= last_x or x > 1.000001 or (probability and y > 1.0):
                raise ValueError("Invalid ordered " + label + " points")
            last_x = x


def load_ammunition(path, selected, mechanism_count):
    profiles = read_json(path)
    if isinstance(profiles, dict):
        profiles = [profiles]
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("Ammo JSON must contain one profile or a nonempty profile list")
    names = set()
    result = []
    for profile in profiles:
        name = safe_id(profile["name"])
        if name in names:
            raise ValueError("Duplicate ammunition ID: " + name)
        names.add(name)
        if selected and name not in selected:
            continue
        if profile["mode"] not in ("KE", "CE"):
            raise ValueError("Ammo mode must be KE or CE")
        for field in ("penetration_mm", "dispersion", "damage_caliber_mm", "air_loss_mm_per_m"):
            finite_number(profile[field], name + "." + field)
        if profile["dispersion"] >= 1.0:
            raise ValueError("dispersion must be in [0, 1)")
        mechanism = profile["mechanism_id"]
        if isinstance(mechanism, bool) or not isinstance(mechanism, int) or not 0 <= mechanism < mechanism_count:
            raise ValueError("mechanism_id is outside the exported mechanism registry")
        validate_table(profile["slope_rows"], name + ".slope_rows")
        validate_table(profile["ricochet_rows"], name + ".ricochet_rows", probability=True)
        curve = profile.get("normalized_distance_curve", [])
        last_x, last_y = -math.inf, math.inf
        for x, y in curve:
            x = finite_number(x, name + ".range distance")
            y = finite_number(y, name + ".range multiplier")
            if x <= last_x or y > last_y or y > 1.0:
                raise ValueError("Range distance must increase; multiplier must not increase")
            last_x, last_y = x, y
        result.append(profile)
    if selected and set(selected) - names:
        raise ValueError("Unknown ammunition ID: " + ", ".join(sorted(set(selected) - names)))
    if not result:
        raise ValueError("No ammunition selected")
    return result


def make_plan(data, args):
    plan = dict(read_json(data / "view.json"))
    if args.angles is not None:
        angles = args.angles
        if not angles or any(not math.isfinite(v) or not 0 <= v < 360 for v in angles):
            raise ValueError("Angles must be finite values in [0, 360)")
        if len(set(angles)) != len(angles):
            raise ValueError("Duplicate angles")
    else:
        if args.step <= 0 or args.step >= 360 or 360 % args.step:
            raise ValueError("--step must be a positive integer divisor of 360, smaller than 360")
        angles = list(range(0, 360, args.step))
    angles = [int(v) if float(v).is_integer() else v for v in angles]
    pitch = finite_number(args.pitch, "pixel pitch")
    if pitch <= 0:
        raise ValueError("--pitch must be greater than zero")
    width = max(1, math.ceil(plan["world_width_m"] / pitch - 1e-10))
    height = max(1, math.ceil((plan["top_y_m"] - plan["bottom_y_m"]) / pitch - 1e-10))
    if width * height * len(angles) >= 2**31 - 1:
        raise ValueError("Too many samples for the current int32 CSR representation")
    plan.update(pixel_pitch_m=pitch, width=width, height=height, world_width_m=width * pitch,
                bottom_y_m=plan["top_y_m"] - height * pitch, angles_deg=angles)
    return plan


def parser():
    ap = argparse.ArgumentParser(description="GRP -> V2 multi-angle conditional penetration maps (CUDA)")
    inputs = ap.add_mutually_exclusive_group()
    inputs.add_argument("--grp", nargs="+", type=Path, help="One or more original GRP files; identify by SHA256")
    inputs.add_argument("--grp-dir", type=Path, help="Read *.grp in this directory; default: bundled examples/grp")
    ap.add_argument("--vehicle", nargs="+", help="Filter vehicle IDs")
    ap.add_argument("--data", type=Path, default=PACKAGE / "data", help="Version-matched vehicle/material/runtime data")
    ap.add_argument("--list-vehicles", action="store_true", help="List supported IDs and hashes without loading CUDA")
    views = ap.add_mutually_exclusive_group()
    views.add_argument("--step", type=int, default=15, help="Horizontal yaw step in degrees (default: 15)")
    views.add_argument("--angles", nargs="+", type=float, help="Explicit yaw angles, e.g. 0 15 22.5 90")
    ap.add_argument("--pitch", type=float, default=0.025, help="True ray sample spacing in metres (default: 0.025)")
    ap.add_argument("--ammo-json", type=Path, help="Ammo profile JSON; default: data/ammunition.json")
    ap.add_argument("--ammo", nargs="+", help="Filter ammo IDs; default: all profiles")
    ap.add_argument("--out", type=Path, default=Path("results"), help="Output directory (default: ./results)")
    ap.add_argument("--cache-dir", type=Path, help="Reusable geometry cache; default: OUT/cache")
    ap.add_argument("--rebuild", action="store_true", help="Rebuild matching geometry cache")
    ap.add_argument("--build-only", action="store_true", help="Build geometry caches without evaluating ammo")
    ap.add_argument("--device", type=int, default=0, help="CUDA device number (default: 0)")
    ap.add_argument("--ray-batch", type=int, default=128, help="Geometry query batch size (default: 128)")
    ap.add_argument("--workers", type=int, default=4, help="PNG export threads (default: 4)")
    ap.add_argument("--hd-scale", type=int, default=8, help="Nearest-neighbour display scale; 0 disables HD PNGs")
    ap.add_argument("--no-images", action="store_true", help="Export raw arrays only")
    ap.add_argument("--uncompressed-arrays", action="store_true", help="Use uncompressed NPZ for faster disk export")
    return ap


def main(argv=None):
    ap = parser()
    args = ap.parse_args(argv)
    data = args.data.resolve()
    profile_paths = sorted((data / "vehicles").glob("*.json"))
    profiles = [(path, read_json(path)) for path in profile_paths]
    if not profiles:
        raise ValueError("No vehicle profiles in " + str(data / "vehicles"))
    for _, profile in profiles:
        safe_id(profile["vehicle"])
    if args.list_vehicles:
        for _, profile in profiles:
            print(profile["vehicle"], profile["display_name"], profile["grp_sha256"])
        return 0
    if args.ray_batch < 1 or args.workers < 1 or not 0 <= args.hd_scale <= 16 or args.device < 0:
        raise ValueError("Invalid batch size, workers, HD scale or CUDA device")
    plan = make_plan(data, args)
    mechanisms = read_json(data / "mechanisms.json")
    ammunition = [] if args.build_only else load_ammunition(args.ammo_json or data / "ammunition.json", args.ammo, len(mechanisms))
    by_hash = {profile["grp_sha256"]: (path, profile) for path, profile in profiles}
    if len(by_hash) != len(profiles):
        raise ValueError("Duplicate GRP hashes in vehicle data")
    wanted = set(args.vehicle or [])
    known_ids = {profile["vehicle"] for _, profile in profiles}
    if wanted - known_ids:
        raise ValueError("Unknown vehicle ID: " + ", ".join(sorted(wanted - known_ids)))
    files = args.grp if args.grp is not None else sorted((args.grp_dir or PACKAGE / "examples" / "grp").glob("*.grp"))
    if not files:
        raise ValueError("No GRP files found; use --grp or --grp-dir")
    selected, seen = [], set()
    for path in files:
        digest = sha_file(path)
        if digest not in by_hash:
            raise ValueError(f"{path.name}: no version-matched material/binding/runtime profile for SHA256 {digest}")
        profile_path, profile = by_hash[digest]
        vehicle = profile["vehicle"]
        if (wanted and vehicle not in wanted) or vehicle in seen:
            continue
        seen.add(vehicle)
        selected.append((path.resolve(), profile_path, profile))
    if wanted - seen:
        raise ValueError("Selected GRP input is missing vehicle(s): " + ", ".join(sorted(wanted - seen)))
    if not selected:
        raise ValueError("No matching vehicles selected")

    import numpy as np
    import cupy as cp
    from grp_geometry import build_or_load
    from grp_export import export_images, write_gallery
    from wt_armor.layered_penetration_v2 import PreparedVehicle

    cp.cuda.Device(args.device).use()
    cp.cuda.runtime.getDeviceProperties(args.device)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cache = (args.cache_dir or out / "cache").resolve()
    report = {"algorithm": "layered_probability_v2", "game_probability_validated": False,
              "client_version": "2.59.0.86", "datamine_commit": "aa326f10c6ba7d523647572368ead2e4d51ffb4d",
              "plan": plan, "ammunition": ammunition, "vehicles": [], "groups": [],
              "status_legend": {"0": "background", "1": "no inner target on ray", "2": "unknown geometry/material", "3": "conditional model estimate"},
              "material_response_flags": {"1": "NERA family", "2": "reactive armor", "4": "source spaced/grille air", "8": "variable thickness"},
              "required_charge_is_RHAe": False}
    started = time.perf_counter()
    print(f"True samples: {plan['width']} x {plan['height']} x {len(plan['angles_deg'])} views; pitch {args.pitch:g} m", flush=True)
    for grp_path, profile_path, profile in selected:
        vehicle = profile["vehicle"]
        t = time.perf_counter()
        metadata, reused = build_or_load(grp_path, profile_path, data, plan, cache, args.rebuild, args.ray_batch)
        item = {"vehicle": vehicle, "grp_sha256": profile["grp_sha256"],
                "layer_metadata": str(metadata), "geometry_cache_reused": reused,
                "geometry_ready_seconds": time.perf_counter() - t}
        report["vehicles"].append(item)
        if args.build_only:
            continue
        t = time.perf_counter()
        model = PreparedVehicle(metadata)
        cp.cuda.get_current_stream().synchronize()
        item["gpu_prepare_seconds"] = time.perf_counter() - t
        for ammo in ammunition:
            key = vehicle + "." + ammo["name"]
            t = time.perf_counter()
            probability, charge, state = model.calculate(ammo)
            calculate_seconds = time.perf_counter() - t
            # Family classification depends on mechanism-dependent material eligibility.
            # Reset only the display metadata cache between profiles; the numeric kernel is unchanged.
            model.risk_cache = None
            risk = model.risk_flags(ammo)
            arrays = out / "arrays" / (key + ".npz")
            arrays.parent.mkdir(exist_ok=True)
            t = time.perf_counter()
            save = np.savez if args.uncompressed_arrays else np.savez_compressed
            save(arrays, penetration_probability=probability, model_required_charge_mm=charge,
                 status=state, material_response_flags=risk, angles_deg=np.asarray(plan["angles_deg"], np.float64),
                 pixel_pitch_m=np.float64(plan["pixel_pitch_m"]), world_width_m=np.float64(plan["world_width_m"]),
                 top_y_m=np.float64(plan["top_y_m"]), bottom_y_m=np.float64(plan["bottom_y_m"]),
                 center_xz_m=np.asarray(profile["view_group"]["center_xz_m"], np.float64))
            array_seconds = time.perf_counter() - t
            t = time.perf_counter()
            images = [] if args.no_images else export_images(key, probability, state, risk, plan, out, args.hd_scale, args.workers)
            group = {"key": key, "calculation_host_arrays_seconds": calculate_seconds,
                     "array_export_seconds": array_seconds, "png_export_seconds": time.perf_counter() - t,
                     "array_file": arrays.relative_to(out).as_posix(), "array_sha256": sha_file(arrays),
                     "images": images, "pixel_counts": {str(i): int(np.count_nonzero(state == i)) for i in range(4)}}
            report["groups"].append(group)
            print(f"{key}: calculate {calculate_seconds:.4f} s; arrays {array_seconds:.4f} s; PNG {group['png_export_seconds']:.4f} s", flush=True)
        del model
        cp.get_default_memory_pool().free_all_blocks()
    report["total_seconds"] = time.perf_counter() - started
    (out / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    write_gallery(report, out)
    print("Complete: " + str(out / "index.html"), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, ImportError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
