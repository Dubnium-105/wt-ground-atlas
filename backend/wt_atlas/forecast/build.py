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
import time

from . import MODEL_VERSION
from .aim import skill_nodes
from .data import Atlas, canonical, digest, file_hash, patch_catalog, read_json, validate_config, verify_manifest
from .engine import variant_profiles, Forecast, ShotCache
from .search import Optimizer
from .rankings import table_candidates
from .inventory import Inventory, coverage, discover, bind_geometry
from .versions import apply_version, invalidation, release_gate, selection_identity, snapshot
from .resources import usage
from .statistics import community_annotations
from .profile_history import ProfileHistory


def compact(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in ("cases", "keys")}


def package_site(out: Path, report: dict, worlds: dict, migrations: list, root: Path) -> None:
    """Atomic, bounded static release. Raw arrays and cache DB never enter the site."""
    if any(p.is_symlink() for p in (out,*out.parents)): raise ValueError("Symlink output not supported")
    out = out.resolve()
    if root.resolve().is_relative_to(out) or any(out.is_relative_to(root/name) for name in ('backend','web','config','scripts','tests','docs','data','.git')):
        raise ValueError('Output must be separate from sources and offline data')
    if out.exists() and any(out.iterdir()) and not (out / ".wt-forecast-site").exists():
        raise ValueError("Refuse to overwrite an unmarked directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=out.name + ".stage-", dir=out.parent))
    backup = out.with_name(out.name + ".previous")
    try:
        source = root / "web" / "forecast"
        for name in ("index.html", "app.mjs", "views.mjs", "styles.css"):
            shutil.copyfile(source / name, stage / name)
        (stage / ".wt-forecast-site").write_text(MODEL_VERSION)
        def write_json(name, data):
            raw = canonical(data).encode()
            checksum = digest(data)
            filename = name + "." + checksum[:16] + ".json"
            path = stage / "data" / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            compressed=gzip.compress(raw,mtime=0)
            path.with_suffix(path.suffix + ".gz").write_bytes(compressed)
            return {"url": "data/" + filename, "sha256": checksum, "bytes": len(raw),
                    "gzip_url":"data/"+filename+'.gz',"gzip_bytes":len(compressed)}
        migration_groups = {}
        for migration in migrations:
            group = (migration['nation'], migration['slots'], migration['acquisition'], migration['before']['actual_br'])
            migration_groups.setdefault(group, []).append(migration)
        migration_tables = []
        for group, rows in sorted(migration_groups.items()):
            summaries = []
            for row in rows:
                summary = {k: row[k] for k in ('status', 'fixed_lineup_delta', 'adaptation_gain_same_new_BR') if k in row}
                for state in ('before', 'after_fixed'):
                    if row.get(state):
                        summary[state] = {'actual_br': row[state]['actual_br']}
                summaries.append(summary)
            migration_tables.append({'nation': group[0], 'slots': group[1], 'acquisition': group[2],
                                     'summaries': summaries, **write_json('migration.' + digest(group)[:16], rows)})
        index = {"schema_version": "1.0.0", "report": write_json("report", report), "worlds": {},
                 "migrations": write_json("migrations", {'schema_version': '1.0.0', 'kind': 'migration_index', 'tables': migration_tables}),
                 "build_key": report["build_key"]}
        for version, world in worlds.items():
            descriptors = [];details = {}
            for table in world["tables"]:
                identity = f'{version}.{table["nation"]}.{table["slots"]}.{table["acquisition"]}.{table["cap_step"]}'
                descriptors.append({"nation": table["nation"], "slots": table["slots"],
                                    "acquisition": table["acquisition"], "br": round(table["cap_step"] / 3, 1),
                                    **write_json(identity, table)})
                for row in table_candidates(table):details[row["lineup_key"]]=descriptors[-1]
            view_files = {}
            for view, boards in world.get('views', {}).items():
                if view == 'global':
                    continue
                view_files[view] = {}
                for board, rows in boards.items():
                    eligible = [] if report.get('sample_subset') else rows
                    view_files[view][board] = write_json(
                        f'{version}.view.{view}.{board.replace("/", ".")}',
                        [{**compact(r), 'detail': details.get(r['lineup_key'])} for r in eligible])
            overview = {"boards": {k: [{**compact(r),'detail':details.get(r['lineup_key'])} for r in rows] for k, rows in world["boards"].items()},
                        "research_boards":{k:[{**compact(r),'detail':details.get(r['lineup_key'])} for r in rows] for k,rows in world.get('research_boards',{}).items()},
                        "views": view_files, "tables": descriptors, "evaluations": world["evaluations"],
                        "truncated_searches": world["truncated_searches"], "global_optimum_guaranteed": False}
            index["worlds"][version] = write_json(version + ".overview", overview)
        (stage / "index.json").write_text(canonical(index), encoding="utf-8")
        budgets=report.get('publication_budgets',{'site_bytes':104857600,'file_bytes':8388608,'gzip_file_bytes':2097152})
        sizes=[(p,p.stat().st_size) for p in stage.rglob('*') if p.is_file()]
        if sum(s for _,s in sizes)>budgets['site_bytes']:raise ValueError('Forecast site byte budget exceeded')
        if any(s>budgets['gzip_file_bytes' if p.suffix=='.gz' else 'file_bytes'] for p,s in sizes):raise ValueError('Forecast shard byte budget exceeded')
        if backup.exists():
            if backup.is_symlink() or not (backup/'.wt-forecast-site').is_file() or backup.parent!=out.parent:raise ValueError('Unsafe publication backup')
            shutil.rmtree(backup)
        if out.exists(): os.replace(out, backup)
        try:
            os.replace(stage, out)
        except BaseException:
            if backup.exists(): os.replace(backup, out)
            raise
    finally:
        if stage.exists(): shutil.rmtree(stage)


def build(args, root: Path) -> dict:
    started=time.perf_counter()
    config_path = Path(args.config).resolve()
    config = read_json(config_path)
    for name in ('nations','br_range'):
        value=getattr(args,name,None)
        if value:config['search'][name]=value.split(',') if name=='nations' else value
    if getattr(args,'slots',None):config['search']['slots']=args.slots
    if getattr(args,'max_evaluations',None):config['search']['max_evaluations']=args.max_evaluations
    if getattr(args,'skill',None):config['population']=config['skill_profiles'][args.skill]
    if getattr(args,'preference',None):config['spawn'].update(config['task_profiles'][args.preference])
    validate_config(config)
    if args.sample_root:
        sample = Path(args.sample_root).resolve()
        integrity = verify_manifest(sample)
        catalog_path = sample / "catalog" / "vehicles.sample.json"
        ammo_path = sample / "ammunition" / "all_resolved_profiles_final_with_apcr_gun_caliber.json"
        atlas_root = sample / "v2"
    else:
        if not all((args.catalog, args.ammunition, args.atlas)):
            if any((args.catalog,args.ammunition,args.atlas)):raise ValueError('Provide all three explicit physical inputs')
            found=discover([Path(os.environ.get('WT_ATLAS_DATA_ROOT',root))])
            if all(len(found[k])==1 for k in ('catalogs','ammunition','atlases')):
                args.catalog,args.ammunition,args.atlas=(found[k][0] for k in ('catalogs','ammunition','atlases'))
            else:
                return empty_build(args,root,config,found)
        catalog_path, ammo_path, atlas_root = (Path(x).resolve() for x in (args.catalog, args.ammunition, args.atlas))
        integrity = {"status": "per_map_hashes", "files": None}
    if Path(args.out).is_symlink(): raise ValueError("Symlink output not supported")
    out = Path(args.out).resolve()
    for source in (atlas_root, catalog_path, ammo_path):
        if source == out or source.is_relative_to(out): raise ValueError("Output would replace an input")
    baseline = read_json(catalog_path)
    community=community_annotations(read_json(Path(args.community)),{v['id'] for v in baseline['vehicles']}) if getattr(args,'community',None) else None
    if community and community['provenance']['game_version']!=snapshot(baseline)['game_version']:raise ValueError('Community snapshot version mismatch')
    if getattr(args,'snapshot_info',None):baseline['snapshot'].update(read_json(Path(args.snapshot_info)))
    patch = read_json(Path(args.scenario)) if args.scenario else None
    planned_inputs = (args.planned_catalog, args.planned_atlas, args.planned_ammunition)
    if any(planned_inputs) and not all(planned_inputs):
        raise ValueError("Planned physical snapshot requires all three --planned-* inputs")
    if any(planned_inputs) and patch is not None:
        raise ValueError("Use a patch OR a complete planned snapshot, not ambiguous combined mutations")
    original_baseline=baseline
    history=ProfileHistory(getattr(args,'profile_history',None) or [])
    planned_history=ProfileHistory(getattr(args,'planned_profile_history',None) or [])
    baseline, planned, audit = apply_version(baseline, patch)
    index=None
    if getattr(args,'index',None):
        index=Inventory(Path(args.index))
        bindings={}
        if getattr(args,'bindings',None):
            binding_report=read_json(Path(args.bindings))
            bindings,binding_errors=bind_geometry(Path(binding_report['repository']),Path(binding_report['jobs']),baseline)
        if not getattr(args,'_reuse_index',False):index.sync(atlas_root,ammo_path,baseline,bindings=bindings,force=getattr(args,'force',False),profile_history=history)
        if getattr(args,'preflight',False):index.verify(atlas_root,max_workers=args.max_workers,memory_budget=args.memory_budget*1024**2,resume=args.resume and not args.force)
    atlas = Atlas(atlas_root, ammo_path, baseline,index=index,memory_budget=args.memory_budget*1024**2,profile_history=history)
    planned_atlas = atlas;planned_index=None
    if all(planned_inputs):
        planned = read_json(Path(args.planned_catalog))
        if index:
            planned_index=Inventory(Path(args.index).with_name(Path(args.index).stem+'.planned.sqlite'))
            planned_bindings={}
            if getattr(args,'planned_bindings',None):
                br=read_json(Path(args.planned_bindings))
                planned_bindings,_=bind_geometry(Path(br['repository']),Path(br['jobs']),planned)
            if not getattr(args,'_reuse_index',False):planned_index.sync(Path(args.planned_atlas),Path(args.planned_ammunition),planned,bindings=planned_bindings,force=args.force,profile_history=planned_history)
            if args.preflight:planned_index.verify(Path(args.planned_atlas),max_workers=args.max_workers,memory_budget=args.memory_budget*1024**2,resume=args.resume and not args.force)
        planned_atlas = Atlas(Path(args.planned_atlas), Path(args.planned_ammunition), planned,index=planned_index,memory_budget=args.memory_budget*1024**2,profile_history=planned_history)
        planned["forecast_scenario"] = {"status": args.planned_status, "id": "separate_physical_snapshot"}
    has_planned = planned is not None or all(planned_inputs)
    cache = ShotCache(Path(args.cache))
    effects = Path(args.effects).resolve() if args.effects else None
    missions = Path(args.missions).resolve() if args.missions else None
    code_hashes = {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    code_hashes['../npz_io.py'] = file_hash(Path(__file__).parents[1]/'npz_io.py')
    inputs = {"catalog": file_hash(catalog_path), "ammunition": file_hash(ammo_path), "config": file_hash(config_path),
              'profile_history':history.signature,'planned_profile_history':planned_history.signature if all(planned_inputs) else None,
              "patch": digest(patch), "maps": digest(sorted((k, m["metadata_sha256"]) for k, m in atlas.meta.items())),
              "effects": file_hash(effects) if effects else None, "missions": file_hash(missions) if missions else None,
              "planned_effects":file_hash(Path(args.planned_effects)) if getattr(args,'planned_effects',None) else None,
              "planned_missions":file_hash(Path(args.planned_missions)) if getattr(args,'planned_missions',None) else None,
              "planned_catalog": file_hash(Path(args.planned_catalog)) if args.planned_catalog else None,
              "planned_ammunition": file_hash(Path(args.planned_ammunition)) if args.planned_ammunition else None,
              "planned_status": args.planned_status if all(planned_inputs) else None,
              "planned_maps": digest(sorted((k, m["metadata_sha256"]) for k, m in planned_atlas.meta.items())) if all(planned_inputs) else None,
              "code": code_hashes,"runtime_config":digest(config)}
    coverage_report=coverage(baseline,atlas,config,index)
    if getattr(args,'dry_run',False):
        cache.close()
        if index:index.close()
        return {'dry_run':True,'build_key':digest(inputs),'coverage':coverage_report,'why_rebuilt':invalidation(original_baseline,planned or baseline),'outputs_written':False}
    engines = {}; optimizers = {}; worlds = {}; diagnostics = []; migrations = []
    try:
        for version, catalog in [("current", baseline)] + ([("planned", planned)] if has_planned else []):
            version_effects=Path(args.planned_effects) if version=='planned' and getattr(args,'planned_effects',None) else effects
            version_missions=Path(args.planned_missions) if version=='planned' and getattr(args,'planned_missions',None) else missions
            engine = Forecast(catalog, atlas if version == "current" else planned_atlas, config, cache, version_effects, version_missions)
            engine.build_key=digest([inputs,version]);engine.resume=getattr(args,'resume',False) and not getattr(args,'force',False)
            if engine.resume:
                # Resuming a whole search table must not bypass source NPZ hashing.
                for map_id in engine.atlas.meta:engine.atlas.verify(map_id)
            engines[version] = engine
            optimizer = Optimizer(engine); optimizers[version] = optimizer
            worlds[version] = optimizer.world()
        # Native-distance diagnostics exercise all supplied views, independently of ranking scenarios.
        if args.diagnostics:
            engine = engines["current"]
            for map_id, meta in atlas.meta.items():
                arrays = atlas.load(map_id)
                attackers = [v for v in engine.variants if any(p["name"] == meta["ammo_id"] for p in variant_profiles(v))]
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
                    after_selections={selection_identity(v):v['key'] for v in engines['planned'].variants if v.get('selection_ready',True)}
                    mapped=[after_selections.get(selection_identity(engines['current'].by_key[k])) for k in old['keys']]
                    if not all(mapped):
                        migrations.append({"nation": old["nation"], "slots": table["slots"], "acquisition": table["acquisition"], "before": compact(old), "members": old["members"], "status": "configuration_removed_or_illegal"}); continue
                    fixed = after.evaluate(tuple(sorted(mapped)))
                    next_rows = [r for t in worlds["planned"]["tables"] if not t["budget_exhausted"] if t["nation"] == old["nation"] and t["slots"] == table["slots"] and t["acquisition"] == table["acquisition"] for r in t["candidates"] if r["actual_br"] == fixed["actual_br"] and r["score"] is not None]
                    best = max(next_rows, key=lambda r: r["score"], default=None)
                    delta = fixed["score"] - old["score"] if not table["budget_exhausted"] and fixed["score"] is not None and old["score"] is not None else None
                    gain = best["score"] - fixed["score"] if delta is not None and best and fixed["score"] is not None else None
                    migrations.append({"nation": old["nation"], "slots": table["slots"], "acquisition": table["acquisition"],
                                       "status": "search_truncated" if table["budget_exhausted"] else "comparable" if delta is not None else "insufficient_evidence",
                                       "before": compact(old), "after_fixed": compact(fixed), "after_optimized": compact(best) if best else None,
                                       "fixed_lineup_delta": delta, "adaptation_gain_same_new_BR": gain})
                    migrations[-1]['attribution']={'direct_changes':[r for r in audit if r.get('vehicle_id') in {m['vehicle_id'] for m in old['members']}],
                        'opponent_environment_changed':digest(baseline)!=digest(planned),'same_members_preserved':True,
                        'loadout_changed':False,'spawn_assumptions':config['spawn']['status'],
                        'room_tops_before':sorted({r['room_top_step'] for r in old['cases']}),
                        'room_tops_after':sorted({r['room_top_step'] for r in fixed['cases']})}
        sample_only = baseline.get("sample_subset") is True or (has_planned and planned.get("sample_subset") is True)
        if sample_only and not config["publication"]["sample_is_publishable"]:
            for world in worlds.values(): world["boards"] = {k: [] for k in world["boards"]}
        binding_counts = {}
        for m in atlas.meta.values(): binding_counts[m["target_binding"]] = binding_counts.get(m["target_binding"], 0) + 1
        gates={};sensitivity_reports={}
        for version,world in worlds.items():
            e=engines[version]
            active_index=index if version=='current' or not all(planned_inputs) else planned_index
            sensitivity_inputs=digest([inputs,version])
            if getattr(args,'run_sensitivity',False):
                from .sensitivity import run
                sensitivity_reports[version]=run(e,world,inputs_sha256=sensitivity_inputs)
            else:sensitivity_reports[version]=read_json(Path(args.sensitivity)) if getattr(args,'sensitivity',None) else None
            gates[version]=release_gate(e.catalog,e.atlas,config,world,inventory=active_index.summary() if active_index else None,missions=e.missions,
                sensitivity=sensitivity_reports[version],sensitivity_inputs=sensitivity_inputs)
            world['research_boards']=world['boards'] if not sample_only else {k:[] for k in world['boards']}
            if not gates[version]['publishable']:world['boards']={k:[] for k in world['boards']}
        public_coverage=copy.deepcopy({k:v for k,v in coverage_report.items() if k not in ('matrix','groups')})
        if public_coverage.get('inventory'):public_coverage['inventory'].pop('metrics',None)
        report = {"schema_version": "1.1.0", "model_version": config["model_version"], "build_key": digest(inputs), "inputs": inputs,
                  "release_readiness":gates,"sensitivity":sensitivity_reports,"coverage":public_coverage,"community":community,
                  "snapshots":{v:snapshot(e.catalog) for v,e in engines.items()},
                  "why_rebuilt":invalidation(original_baseline,planned or baseline),
                  "publication_budgets":config['publication'].get('budgets',{'site_bytes':104857600,'file_bytes':8388608,'gzip_file_bytes':2097152}),
                  "skill_profile":getattr(args,'skill',None),'task_preference':getattr(args,'preference',None),
                  "client_versions": sorted({m["source_version"] for m in atlas.meta.values()}),
                  "physical_distances_m": sorted({atlas.profiles[m["ammo_id"]]["distance_m"] for m in atlas.meta.values()}),
                  "metadata_count": len(atlas.meta), "catalog_vehicles": len(baseline["vehicles"]),
                  "ammunition_profiles": len(atlas.profiles), "integrity": integrity,
                  "sample_subset": sample_only, "published_rankings": any(g['publishable'] for g in gates.values()),
                  "research_rankings":not sample_only,"synthetic_fixture":baseline.get('test_fixture',False),
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
        reports=Path(args.reports) if getattr(args,'reports',None) else None
        if reports:
            reports.mkdir(parents=True,exist_ok=True)
            for filename,value in [('data-coverage.json',coverage_report),('release-readiness.json',gates),('forecast-validation.json',report)]:
                (reports/filename).write_text(canonical(value),encoding='utf-8')
        return {"out": str(out), "build_key": report["build_key"], "catalog_vehicles": len(baseline["vehicles"]),
                "maps": len(atlas.meta), "diagnostic_rows": len(diagnostics), "cache_hits": cache.hits,
                "cache_misses": cache.misses, "npz_loads": atlas.loads,
                "ranked_counts": {v: sum(len(r) for r in w["boards"].values()) for v, w in worlds.items()},
                "evaluations": {v: w["evaluations"] for v, w in worlds.items()},
                "search_execution":{v:w["execution"] for v,w in worlds.items()},
                "research_counts":{v:sum(len(r) for r in w['research_boards'].values()) for v,w in worlds.items()},
                "release_readiness":gates,"duration_seconds":time.perf_counter()-started,"resources":usage(),
                "publication": "sample_diagnostics_only" if sample_only else 'conditional_research_only' if not report['published_rankings'] else "conditional_model_predictions"}
    finally:
        cache.close()
        if index:index.close()
        if planned_index:planned_index.close()


def empty_build(args,root,config,found):
    gate={'algorithm_ready':True,'data_ready':False,'publishable':False,'why_not_publishable':['physical_inputs_missing_or_ambiguous']}
    report={'build_key':digest([config,found]),'sample_subset':False,'published_rankings':False,'research_rankings':False,
        'metadata_count':0,'catalog_vehicles':0,'physical_distances_m':[],'scenario_status':'not_provided','diagnostics':[],
        'release_readiness':{'current':gate},'coverage':{'discovered':found},'endpoint':config['endpoint'],
        'calibration_status':config['calibration_status'],'limitations':['Provide a same-version catalog, ammunition profiles and V2 atlas.']}
    world={'boards':{f'{s}/{a}':[] for s in config['search']['slots'] for a in config['search']['acquisition_profiles']},'tables':[],'evaluations':0,'truncated_searches':0}
    if not getattr(args,'dry_run',False):package_site(Path(args.out),report,{'current':world},[],root)
    return {'out':args.out,'ranked_counts':{'current':0},'release_readiness':gate,'publication':'input_diagnostics_only'}


def main(argv=None):
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-root")
    parser.add_argument("--catalog"); parser.add_argument("--ammunition"); parser.add_argument("--atlas")
    parser.add_argument("--config", default=str(root / "config" / "forecast.json"))
    parser.add_argument("--scenario"); parser.add_argument("--effects"); parser.add_argument("--missions")
    parser.add_argument("--planned-catalog"); parser.add_argument("--planned-atlas"); parser.add_argument("--planned-ammunition")
    parser.add_argument("--planned-status", choices=["proposed", "announced", "released", "hypothetical"], default="proposed")
    parser.add_argument('--planned-effects');parser.add_argument('--planned-missions');parser.add_argument('--planned-bindings');parser.add_argument('--snapshot-info')
    parser.add_argument('--profile-history',action='append',default=[])
    parser.add_argument('--planned-profile-history',action='append',default=[])
    parser.add_argument('--index',default=str(root/'data/cache/forecast-metadata.sqlite'));parser.add_argument('--bindings');parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--max-workers',type=int,default=2);parser.add_argument('--memory-budget',type=int,default=512,help='MiB for array workers')
    parser.add_argument('--resume',action='store_true');parser.add_argument('--force',action='store_true');parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--nations');parser.add_argument('--br-range',type=float,nargs=2);parser.add_argument('--slots',type=int,nargs='+')
    parser.add_argument('--max-evaluations',type=int);parser.add_argument('--reports');parser.add_argument('--sensitivity')
    parser.add_argument('--skill',choices=['novice','normal','expert']);parser.add_argument('--preference',choices=['ground','balanced','air_support'])
    parser.add_argument('--run-sensitivity',action='store_true',help='Measure five model/search perturbations on exact current inputs')
    parser.add_argument('--all-profiles',action='store_true',help='Precompute all nine skill/task profiles for interactive static switching')
    parser.add_argument('--community',help='Authorized qualitative annotations; never used as score weights')
    parser.add_argument("--cache", default=str(root / "data" / "cache" / "forecast.sqlite"))
    parser.add_argument("--out", default=str(root / "dist" / "forecast"))
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--diagnostic-skills", type=float, nargs="+", default=[-1, 0, 1])
    args = parser.parse_args(argv)
    try:
        if args.all_profiles and not args.dry_run:
            # Build the entire multi-profile publication in isolation before replacing it.
            if any(p.is_symlink() for p in (Path(args.out),*Path(args.out).parents)):raise ValueError('Symlink output')
            final=Path(args.out).resolve();final.parent.mkdir(parents=True,exist_ok=True)
            if root.is_relative_to(final) or any(final.is_relative_to(root/n) for n in ('web','backend','scripts','tests','config','docs','data','.git')):raise ValueError('Unsafe profiles output')
            if any(Path(p).resolve().is_relative_to(final) for p in (args.catalog,args.ammunition,args.atlas,args.sample_root) if p):raise ValueError('Output overlaps an input')
            if final.exists() and any(final.iterdir()) and not (final/'.wt-forecast-site').is_file():raise ValueError('Unmanaged forecast output')
            with tempfile.TemporaryDirectory(prefix='.profiles-',dir=final.parent) as temporary:
                stage=Path(temporary)/'site';results=[];cohorts=[]
                profiles=[('normal','ground')]+[(s,p) for s in ('novice','normal','expert') for p in ('ground','balanced','air_support') if (s,p)!=('normal','ground')]
                for i,(skill,preference) in enumerate(profiles):
                    a=copy.copy(args);a.skill=skill;a.preference=preference;a._reuse_index=i>0
                    a.out=str(stage if i==0 else Path(temporary)/(skill+'-'+preference));a.reports=args.reports if i==0 else None
                    result=build(a,root);results.append(result)
                    cohorts.append({'skill':skill,'preference':preference,'directory':'' if i==0 else 'profiles/'+skill+'-'+preference+'/'})
                for item in cohorts[1:]:shutil.move(str(Path(temporary)/(item['skill']+'-'+item['preference'])),stage/item['directory'])
                for item in cohorts:
                    directory=stage/item['directory'];idx=read_json(directory/'index.json')
                    idx['cohorts']=[{**c,'url':os.path.relpath(stage/c['directory']/'index.html',directory).replace('\\','/')} for c in cohorts]
                    (directory/'index.json').write_text(canonical(idx),encoding='utf-8')
                budgets=read_json(Path(args.config))['publication']['budgets']
                if sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())>budgets['site_bytes']:raise ValueError('Combined profiles exceed site budget')
                previous=final.with_name(final.name+'.previous')
                if previous.exists():
                    if previous.is_symlink() or not (previous/'.wt-forecast-site').is_file():raise ValueError('Unmanaged previous output')
                    shutil.rmtree(previous)
                if final.exists():os.replace(final,previous)
                try:os.replace(stage,final)
                except BaseException:
                    if previous.exists():os.replace(previous,final)
                    raise
            print(json.dumps({'out':str(final),'profiles':results},ensure_ascii=False,indent=2,allow_nan=False))
        else:print(json.dumps(build(args, root), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, OSError, ArithmeticError,TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__": main()
