"""Reproducible CLI: V2 data -> engagement generator -> global next-patch site."""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import os
from pathlib import Path
import shutil
import tempfile

from . import MODEL_VERSION
from .aim import skill_nodes
from .data import Atlas, canonical, digest, file_hash, patch_catalog, read_json, validate_config, verify_manifest
from .engine import Forecast, ShotCache
from .search import Optimizer


def compact(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in ("cases", "keys")}


def package_site(out: Path, report: dict, worlds: dict, migrations: list, root: Path) -> None:
    """Atomic, bounded static release. Raw arrays and cache DB never enter the site."""
    if out.is_symlink(): raise ValueError("Symlink output not supported")
    out = out.resolve()
    if out.exists() and any(out.iterdir()) and not (out / ".wt-forecast-site").exists():
        raise ValueError("Refuse to overwrite an unmarked directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=out.name + ".stage-", dir=out.parent))
    backup = out.with_name(out.name + ".previous")
    try:
        source = root / "web" / "forecast"
        for name in ("index.html", "app.mjs", "styles.css"):
            shutil.copyfile(source / name, stage / name)
        (stage / ".wt-forecast-site").write_text(MODEL_VERSION)
        def write_json(name, data):
            raw = canonical(data).encode()
            checksum = digest(data)
            filename = name + "." + checksum[:16] + ".json"
            path = stage / "data" / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.with_suffix(path.suffix + ".gz").write_bytes(gzip.compress(raw, mtime=0))
            return {"url": "data/" + filename, "sha256": checksum, "bytes": len(raw)}
        index = {"schema_version": "1.0.0", "report": write_json("report", report), "worlds": {},
                 "migrations": write_json("migrations", migrations), "build_key": report["build_key"]}
        for version, world in worlds.items():
            descriptors = []
            for table in world["tables"]:
                identity = f'{version}.{table["nation"]}.{table["slots"]}.{table["acquisition"]}.{table["cap_step"]}'
                descriptors.append({"nation": table["nation"], "slots": table["slots"],
                                    "acquisition": table["acquisition"], "br": round(table["cap_step"] / 3, 1),
                                    **write_json(identity, table)})
            overview = {"boards": {k: [compact(r) for r in rows] for k, rows in world["boards"].items()},
                        "tables": descriptors, "evaluations": world["evaluations"],
                        "truncated_searches": world["truncated_searches"], "global_optimum_guaranteed": False}
            index["worlds"][version] = write_json(version + ".overview", overview)
        (stage / "index.json").write_text(canonical(index), encoding="utf-8")
        if backup.exists(): raise ValueError("Previous publication backup exists; inspect it before retry")
        if out.exists(): os.replace(out, backup)
        try:
            os.replace(stage, out)
        except BaseException:
            if backup.exists(): os.replace(backup, out)
            raise
        if backup.exists(): shutil.rmtree(backup)
    finally:
        if stage.exists(): shutil.rmtree(stage)


def build(args, root: Path) -> dict:
    config_path = Path(args.config).resolve()
    config = read_json(config_path)
    validate_config(config)
    if args.sample_root:
        sample = Path(args.sample_root).resolve()
        integrity = verify_manifest(sample)
        catalog_path = sample / "catalog" / "vehicles.sample.json"
        ammo_path = sample / "ammunition" / "all_resolved_profiles_final_with_apcr_gun_caliber.json"
        atlas_root = sample / "v2"
    else:
        if not all((args.catalog, args.ammunition, args.atlas)):
            raise ValueError("Provide --sample-root OR --catalog, --ammunition, --atlas")
        catalog_path, ammo_path, atlas_root = (Path(x).resolve() for x in (args.catalog, args.ammunition, args.atlas))
        integrity = {"status": "per_map_hashes", "files": None}
    if Path(args.out).is_symlink(): raise ValueError("Symlink output not supported")
    out = Path(args.out).resolve()
    for source in (atlas_root, catalog_path, ammo_path):
        if source == out or source.is_relative_to(out): raise ValueError("Output would replace an input")
    baseline = read_json(catalog_path)
    patch = read_json(Path(args.scenario)) if args.scenario else None
    planned_inputs = (args.planned_catalog, args.planned_atlas, args.planned_ammunition)
    if any(planned_inputs) and not all(planned_inputs):
        raise ValueError("Planned physical snapshot requires all three --planned-* inputs")
    if any(planned_inputs) and patch is not None:
        raise ValueError("Use a patch OR a complete planned snapshot, not ambiguous combined mutations")
    planned, audit = patch_catalog(baseline, patch)
    atlas = Atlas(atlas_root, ammo_path, baseline)
    planned_atlas = atlas
    if all(planned_inputs):
        planned = read_json(Path(args.planned_catalog))
        planned_atlas = Atlas(Path(args.planned_atlas), Path(args.planned_ammunition), planned)
        planned["forecast_scenario"] = {"status": args.planned_status, "id": "separate_physical_snapshot"}
    has_planned = patch is not None or all(planned_inputs)
    cache = ShotCache(Path(args.cache))
    effects = Path(args.effects).resolve() if args.effects else None
    missions = Path(args.missions).resolve() if args.missions else None
    code_hashes = {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    inputs = {"catalog": file_hash(catalog_path), "ammunition": file_hash(ammo_path), "config": file_hash(config_path),
              "patch": digest(patch), "maps": digest(sorted((k, m["metadata_sha256"]) for k, m in atlas.meta.items())),
              "effects": file_hash(effects) if effects else None, "missions": file_hash(missions) if missions else None,
              "planned_catalog": file_hash(Path(args.planned_catalog)) if args.planned_catalog else None,
              "planned_ammunition": file_hash(Path(args.planned_ammunition)) if args.planned_ammunition else None,
              "planned_status": args.planned_status if all(planned_inputs) else None,
              "planned_maps": digest(sorted((k, m["metadata_sha256"]) for k, m in planned_atlas.meta.items())) if all(planned_inputs) else None,
              "code": code_hashes}
    engines = {}; optimizers = {}; worlds = {}; diagnostics = []; migrations = []
    try:
        for version, catalog in [("current", baseline)] + ([("planned", planned)] if has_planned else []):
            engine = Forecast(catalog, atlas if version == "current" else planned_atlas, config, cache, effects, missions)
            engines[version] = engine
            optimizer = Optimizer(engine); optimizers[version] = optimizer
            worlds[version] = optimizer.world()
        # Native-distance diagnostics exercise all supplied views, independently of ranking scenarios.
        if args.diagnostics:
            engine = engines["current"]
            for map_id, meta in atlas.meta.items():
                arrays = atlas.load(map_id)
                attackers = [v for v in engine.variants if v["profile"] and v["profile"]["name"] == meta["ammo_id"]]
                targets = [v for v in engine.variants if v["vehicle_id"] == meta["target_id"]]
                if not attackers or not targets: continue
                for angle in arrays["angles_deg"]:
                    scenario = {**config["scenarios"][0], "yaw_deg": float(angle),
                                "physical_distance_m": atlas.profiles[meta["ammo_id"]]["distance_m"],
                                "aiming_distance_m": atlas.profiles[meta["ammo_id"]]["distance_m"]}
                    for skill in args.diagnostic_skills:
                        shot = engine.shot(attackers[0], targets[0], scenario, skill)
                        diagnostics.append({k: v for k, v in shot.items() if k != "aim_points"})
        if has_planned:
            before, after = optimizers["current"], optimizers["planned"]
            seen = set()
            for table in worlds["current"]["tables"]:
                for old in table["candidates"]:
                    key = (tuple(old["keys"]), table["slots"], table["acquisition"])
                    if key in seen: continue
                    seen.add(key)
                    if not all(k in engines["planned"].by_key for k in old["keys"]):
                        migrations.append({"nation": old["nation"], "members": old["members"], "status": "configuration_removed_or_illegal"}); continue
                    fixed = after.evaluate(tuple(old["keys"]))
                    next_rows = [r for t in worlds["planned"]["tables"] if t["nation"] == old["nation"] and t["slots"] == table["slots"] and t["acquisition"] == table["acquisition"] for r in t["candidates"] if r["actual_br"] == fixed["actual_br"] and r["score"] is not None]
                    best = max(next_rows, key=lambda r: r["score"], default=None)
                    delta = fixed["score"] - old["score"] if fixed["score"] is not None and old["score"] is not None else None
                    gain = best["score"] - fixed["score"] if best and fixed["score"] is not None else None
                    migrations.append({"nation": old["nation"], "slots": table["slots"], "acquisition": table["acquisition"],
                                       "before": compact(old), "after_fixed": compact(fixed), "after_optimized": compact(best) if best else None,
                                       "fixed_lineup_delta": delta, "adaptation_gain_same_new_BR": gain})
        sample_only = baseline.get("sample_subset") is True or (has_planned and planned.get("sample_subset") is True)
        if sample_only and not config["publication"]["sample_is_publishable"]:
            for world in worlds.values(): world["boards"] = {k: [] for k in world["boards"]}
        binding_counts = {}
        for m in atlas.meta.values(): binding_counts[m["target_binding"]] = binding_counts.get(m["target_binding"], 0) + 1
        report = {"schema_version": "1.0.0", "model_version": config["model_version"], "build_key": digest(inputs), "inputs": inputs,
                  "client_versions": sorted({m["source_version"] for m in atlas.meta.values()}),
                  "physical_distances_m": sorted({atlas.profiles[m["ammo_id"]]["distance_m"] for m in atlas.meta.values()}),
                  "metadata_count": len(atlas.meta), "catalog_vehicles": len(baseline["vehicles"]),
                  "ammunition_profiles": len(atlas.profiles), "integrity": integrity,
                  "sample_subset": sample_only, "published_rankings": not sample_only or config["publication"]["sample_is_publishable"],
                  "source_bindings": binding_counts, "scenario_status": patch.get("status", "unknown") if patch else args.planned_status if has_planned else "not_provided",
                  "map_validation": {"baseline_arrays_verified": len(atlas._verified), "planned_arrays_verified": len(planned_atlas._verified) if has_planned else 0},
                  "scenario_audit": audit, "diagnostics": diagnostics,
                  "rejected_variants": engines["current"].rejected,
                  "calibration_status": config["calibration_status"], "endpoint": config.get("endpoint", "penetration"),
                  "population": config["population"], "matchmaking": config["matchmaking"], "spawn_assumptions": config["spawn"],
                  "ranking_scope": config["publication"]["scope"],
                  "limitations": ["Conditional source-model estimates, not actual kills or win rates.",
                    "Only supplied physical distances and yaw slices are used; no range rescaling from required-charge maps.",
                    "No-reference-slice substitution. Every attack direction needs its own owned-ammunition map.",
                    "Catalog geometry equivalence is not inferred from target_sha256; binding strength is reported.",
                    "Air/AA/recon tasks require versioned mission evidence; absent evidence is not zero or free capability.",
                    "Beam/exchange search has an explicit work budget, not a global optimality guarantee.",
                    "Intervals bound missing map effects, not statistical confidence or all model error."]}
        package_site(out, report, worlds, migrations, root)
        return {"out": str(out), "build_key": report["build_key"], "catalog_vehicles": len(baseline["vehicles"]),
                "maps": len(atlas.meta), "diagnostic_rows": len(diagnostics), "cache_hits": cache.hits,
                "cache_misses": cache.misses, "npz_loads": atlas.loads,
                "ranked_counts": {v: sum(len(r) for r in w["boards"].values()) for v, w in worlds.items()},
                "evaluations": {v: w["evaluations"] for v, w in worlds.items()},
                "publication": "sample_diagnostics_only" if sample_only else "conditional_model_predictions"}
    finally:
        cache.close()


def main(argv=None):
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-root")
    parser.add_argument("--catalog"); parser.add_argument("--ammunition"); parser.add_argument("--atlas")
    parser.add_argument("--config", default=str(root / "config" / "forecast.json"))
    parser.add_argument("--scenario"); parser.add_argument("--effects"); parser.add_argument("--missions")
    parser.add_argument("--planned-catalog"); parser.add_argument("--planned-atlas"); parser.add_argument("--planned-ammunition")
    parser.add_argument("--planned-status", choices=["proposed", "announced", "released", "hypothetical"], default="proposed")
    parser.add_argument("--cache", default=str(root / "data" / "cache" / "forecast.sqlite"))
    parser.add_argument("--out", default=str(root / "dist" / "forecast"))
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--diagnostic-skills", type=float, nargs="+", default=[-1, 0, 1])
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build(args, root), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, OSError, ArithmeticError) as exc:
        parser.error(str(exc))


if __name__ == "__main__": main()
