# Internal integration contract

No Sites. Public frontend is plain static HTML/CSS/ES modules and Web Worker. Python backend performs offline acquisition/build/export. All game values, model weights, thresholds and tunables belong to versioned data/config, not rendering or algorithm source. Mathematical identities, indices and protocol constants are not game data. Unknown numerical values are null.

## Normalized catalog (`data/snapshots/catalog.json`)
Top level: schema_version, snapshot {id,collected_at,source_url,game_version,commit,coverage}, nations [{id,label}], vehicles [].
Vehicle: id (stable wiki/datamine ID), name, nation, kind (ground|aircraft|helicopter), role, rank, branch, folder, predecessors[], special (bool|null), br {current:number|null,planned:number|null}, wiki_url, image_url, configuration_id, equivalence_group (null unless independently verified equivalent), mobility {forward_kmh,reverse_kmh,power_hp,mass_t,power_to_weight_hp_t,neutral_steering}, firepower {reload_ace_s,autoload_s,reload_type,depression_deg,elevation_deg,traverse_deg_s,vertical_deg_s}, protection {front_mm,side_mm,rear_mm,era,spall_liner}, sensors {}, air_defence {}, ammo [{id,name,type,caliber_mm,penetration:[{distance_m,angle_deg,mm}],provenance}], loadouts [{id,name,legal,weapons:[],spawn_cost,provenance}], provenance[], missing[]. Nested fields may be absent/null; never default physical values to zero. Catalog uses only Ground RB BR. Known Air RB must not be substituted. Dates come from captured metadata. Full source extensions/raw data are allowed.

## Changes (`data/snapshots/changes.json`)
{schema_version,status,source_url,collected_at,entries:[{vehicle_id,field,old,new,source_row,description}],audit:[]}. Fields use dotted normalized paths. Source unavailable: entries=[] and explicit status/reason; no fake changes. Frontend also accepts local scenario import. Source scenario must be marked simulated.

## Core engine (`shared/engine.mjs`, `config/model.json`)
Exports: applyScenario(catalog,scenario,version), getBRs(vehicles, nation,version), matchEnvironment(vehicles,lineup,version,config), evaluatePair(attacker,target,context,config,probabilities), evaluateLineup(vehicles,lineupIds,context,config,probabilities), recommendNation(vehicles,nation,context,config,probabilities), compareVersions(vehicles,lineupIds,scenario,context,config,probabilities), validateLoadout(vehicle,loadout,context,config). Context fields {version,nation,br,slots,skill,distance_m,angle_deg,includeSpecial,availableIds}. Return rich explanations, missing fields, data coverage; never label scores win rates. Shared engine runs in browser Worker and node precompute. Engine owner can finalize return shape and coordinate root.

## Probability export (`web/data/probability/index.json`)
Backend owns format; root reads returned schema before integrating. Dynamic dimensions, angles and spacing; status semantics preserved. Export per-vehicle/ammo/angle small shards (JSON probability quantization + statuses or binary), metadata with full physical key, references are never vehicle ammunition availability. Backend writes `web/data/probability`, root builds other web/data.

## Source/build ownership
Source agent: backend/wt_atlas/sources.py and supporting source modules, config/sources.json, data/snapshots/catalog.json and changes.json, source audits/docs/tests.
Engine agent: shared/engine.mjs, config/model.json, scripts/build_decisions.mjs, tests/engine.test.mjs; communicate output schema.
Backend agent: backend/wt_atlas remaining modules, CLI/GRP pipeline/export, requirements, Python probability/pipeline tests. Coordinate sources.py import; do not edit agent source or engine files.
Root: web UI, integration scripts/docs/deploy packaging, final QA.
