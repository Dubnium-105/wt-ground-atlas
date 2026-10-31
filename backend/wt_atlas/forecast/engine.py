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
from .capabilities import loadout_errors, validate_mission
from .data import Atlas, br_step, canonical, digest, file_hash, number, read_json, safe_file, taxonomy
from ..npz_io import load_npz_bounded


def variant_profiles(variant: dict) -> tuple:
    """Physical datasets are contexts of a selected round, not extra loadouts."""
    return tuple(variant.get('profiles', (variant['profile'],) if variant.get('profile') else ()))


def ammunition_payloads(bindings):
    grouped = {}
    for ammo, profile in bindings:
        distance = profile.get('distance_m')
        if not number(distance) or distance < 0:
            continue  # Unknown physical distance cannot support a modeled shot.
        stored_ammo, by_distance = grouped.setdefault(ammo['id'], (ammo, {}))
        if distance in by_distance and digest(by_distance[distance]) != digest(profile):
            raise ValueError('Ambiguous ammunition distance: ' + ammo['id'] + ' at ' + str(distance))
        by_distance[distance] = profile
    return [(ammo, tuple(profiles[d] for d in sorted(profiles)), None)
            for ammo, profiles in grouped.values()]


def enumerate_variants(catalog: dict, atlas: Atlas, config: dict | None = None) -> tuple[list[dict], list[dict]]:
    variants, rejected = [], []
    config = config or {}
    version = catalog.get('snapshot', {}).get('game_version', catalog.get('snapshot', {}).get('client_source', {}).get('version', 'unknown'))
    for vehicle in sorted(catalog["vehicles"], key=lambda v: v["id"]):
        if vehicle.get('system_role') == 'component':
            rejected.append({'vehicle_id':vehicle['id'],'reason':'multi_vehicle_system_component'}); continue
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
            ownership = config.get('ownership', {})
            unlocked = ownership.get('unlocked_modifications_by_vehicle', {}).get(vehicle['id'])
            if ownership.get('require_unlocked') and (unlocked is None or not set(option.get('required_modification_ids', [])) <= set(unlocked)):
                rejected.append({'vehicle_id':vehicle['id'],'reason':'modifications_unavailable','rating_configuration_id':option['id']}); continue
            br = option.get("br", {}).get("current")
            if not number(br):
                rejected.append({"vehicle_id": vehicle["id"], "reason": "unknown_ground_rb"}); continue
            br_step(br)
            payloads = ammunition_payloads(bindings) if domain == "ground" else []
            if domain == "air":
                for loadout in vehicle.get("loadouts", []):
                    errors = loadout_errors(loadout, vehicle, option, unlocked=unlocked, require_unlocked=ownership.get('require_unlocked',False))
                    if errors:
                        rejected.append({'vehicle_id':vehicle['id'],'loadout_id':loadout.get('id'),'reasons':errors}); continue
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
            if not payloads:
                payloads = [(None, None, None)]
                rejected.append({'vehicle_id':vehicle['id'],'rating_configuration_id':option['id'],
                    'reason':'unknown_owned_ammunition_configuration' if domain=='ground' else 'no_verified_legal_carried_loadout',
                    'scope':'own_selection','opponent_population_retained':True})
            for ammo, profiles, loadout in payloads:
                profiles = profiles or ()
                profile = profiles[0] if profiles else None
                if block is not None:
                    has_policy = any(k in option for k in ("allowed_loadout_ids", "allowed_weapon_ids", "allowed_ammo_ids"))
                    if not has_policy: continue
                    if "allowed_loadout_ids" in option and (loadout is None or loadout["id"] not in option["allowed_loadout_ids"]): continue
                    if "allowed_weapon_ids" in option and (loadout is None or any(w["id"] not in option["allowed_weapon_ids"] for w in loadout["weapons"])): continue
                    if "allowed_ammo_ids" in option and (ammo is None or ammo["id"] not in option["allowed_ammo_ids"]): continue
                identity = {"vehicle_id": vehicle["id"], "mode": "ground_rb", "version": version, "rating_configuration_id": option["id"],
                            "ammo_id": ammo["id"] if ammo else None, "loadout_id": loadout["id"] if loadout else None}
                variants.append({**identity, "key": digest(identity), "vehicle": vehicle, "ammo": ammo,
                                 "profile": profile, "profiles": profiles, "loadout": loadout, "nation": vehicle["nation"],
                                 "br": br, "br_step": br_step(br), "domain": domain,
                                 "selection_ready": bool(ammo and profiles) if domain=='ground' else loadout is not None,
                                 "classification": taxonomy(vehicle), "system_id": vehicle.get("system_id")})
    return sorted(variants, key=lambda v: v["key"]), rejected


class ShotCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS shots (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        if 'sha256' not in {r[1] for r in self.db.execute('PRAGMA table_info(shots)')}:
            self.db.execute('ALTER TABLE shots ADD COLUMN sha256 TEXT')
        self.db.execute('CREATE TABLE IF NOT EXISTS derived (key TEXT PRIMARY KEY, value TEXT NOT NULL, sha256 TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS evidence (key TEXT PRIMARY KEY, level TEXT NOT NULL, value TEXT NOT NULL, sha256 TEXT NOT NULL)')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.hits, self.misses = 0, 0

    def get(self, key: str):
        record = self.db.execute("SELECT value,sha256 FROM shots WHERE key=?", (key,)).fetchone()
        if record:
            import json
            try:
                value = json.loads(record[0], parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
                if not isinstance(value,dict) or digest(value)!=record[1]: raise ValueError('Invalid cache record')
                self.hits += 1; return value
            except (ValueError,TypeError):
                self.db.execute('DELETE FROM shots WHERE key=?',(key,))
        self.misses += 1; return None

    def put(self, key: str, value: dict):
        self.db.execute("INSERT OR REPLACE INTO shots VALUES (?,?,?)", (key, canonical(value),digest(value)))
        if self.misses % 128 == 0: self.db.commit()

    def close(self):
        self.db.commit(); self.db.close()

    def derived_get(self,key):
        import json
        row=self.db.execute('SELECT value,sha256 FROM derived WHERE key=?',(key,)).fetchone()
        if row:
            try:
                value=json.loads(row[0])
                if digest(value)!=row[1]:raise ValueError('Derived cache checksum')
                return value
            except (ValueError,TypeError):self.db.execute('DELETE FROM derived WHERE key=?',(key,))
        return None

    def derived_put(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO derived VALUES (?,?,?)',(key,canonical(value),digest(value)))
        self.db.commit()

    def evidence_put(self,key,level,value):
        self.db.execute('INSERT OR REPLACE INTO evidence VALUES (?,?,?,?)',(key,level,canonical(value),digest(value)))

    def evidence_get(self,key):
        import json
        row=self.db.execute('SELECT level,value,sha256 FROM evidence WHERE key=?',(key,)).fetchone()
        if row:
            value=json.loads(row[1])
            if digest(value)!=row[2]:raise ValueError('Evidence checksum failure')
            return {'level':row[0],'value':value}
        value=self.get(key)
        return {'level':'shot','value':value} if value else None


class Forecast:
    def __init__(self, catalog: dict, atlas: Atlas, config: dict, cache: ShotCache,
                 effects_path: Path | None = None, missions_path: Path | None = None):
        self.catalog, self.atlas, self.config, self.cache = catalog, atlas, config, cache
        self.variants, self.rejected = enumerate_variants(catalog, atlas, config)
        self.by_key = {v["key"]: v for v in self.variants}
        self.skills = skill_nodes(config["population"])
        self.enemy_skills = skill_nodes(config.get("opponent_population", config["population"]))
        self.aim_code_hash = digest({name:file_hash(Path(__file__).parent/name)
                                    for name in ('aim.py','data.py','engine.py','../npz_io.py')})
        self.effects_path, self.effects = effects_path, {}
        self.effect_array = (None, None)
        if effects_path:
            for row in read_json(effects_path)["records"]:
                if row["map_id"] in self.effects: raise ValueError("Duplicate effect kernel")
                if row['map_id'] not in atlas.meta:raise ValueError('Unknown effect map')
                if 'array_file' in row and file_hash(safe_file(effects_path.parent,row['array_file']))!=row['array_sha256']:
                    raise ValueError('Effect array checksum')
                self.effects[row["map_id"]] = row
        self.missions_path = missions_path
        self.missions = {}
        if missions_path:
            for row in read_json(missions_path)["records"]:
                row=validate_mission(row,catalog.get('snapshot',{}).get('game_version',catalog.get('snapshot',{}).get('client_source',{}).get('version')),self.by_key,{s['id'] for s in config['scenarios']})
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
        if row.get('status')=='validated' and row.get('source_version')!=self.atlas.meta[map_id]['source_version']:
            raise ValueError('Effect kernel source version mismatch')
        if "constant" in row:
            if row.get('status')!='assumed':raise ValueError('Constant damage kernel must remain assumed')
            q = np.array(row["constant"], dtype=float)
            if q.shape != (3,): raise ValueError("Invalid effect vector")
            if not np.all(np.isfinite(q)) or np.any(q < 0) or q.sum() > 1+1e-8:
                raise ValueError('Invalid effect probability mass')
            return np.broadcast_to(q, shape + (3,)), digest(row)
        source = safe_file(self.effects_path.parent, row["array_file"])
        if file_hash(source) != row["array_sha256"]: raise ValueError("Effect array checksum")
        identity = (map_id, row['array_sha256'])
        if self.effect_array[0] != identity:
            array = load_npz_bounded(source, ('conditional_outcomes',), self.atlas.memory_budget)['conditional_outcomes']
            physical_shape = self.atlas.load(map_id)['penetration_probability'].shape
            if array.shape != physical_shape+(3,) or array.dtype.kind != 'f' or physical_shape[1:] != shape:
                raise ValueError('Invalid effect array layout')
            if not np.all(np.isfinite(array)) or np.any(array < 0) or np.any(array.sum(axis=-1) > 1+1e-8):
                raise ValueError('Invalid effect probability mass')
            self.effect_array = (identity, array)
        return self.effect_array[1][angle_index], digest(row)

    def shot(self, attacker: dict, target: dict, scenario: dict, skill: float) -> dict | None:
        if attacker["vehicle"].get("forecast_physics_invalidated") or target["vehicle"].get("forecast_physics_invalidated"):
            return None
        profiles = [p for p in variant_profiles(attacker) if p['distance_m'] == scenario['physical_distance_m']]
        if len(profiles) != 1: return None  # Never substitute another physical range.
        profile = profiles[0]
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
        if map_id in self.effects:
            row=self.effects[map_id]
            if 'array_file' in row and file_hash(safe_file(self.effects_path.parent,row['array_file']))!=row['array_sha256']:
                raise ValueError('Effect array checksum')
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
                      source_profile_sha256=m['ammo_profile_sha256'],active_profile_sha256=digest(profile),
                      profile_reconciliation=m.get('profile_reconciliation'),
                      effect_status=self.effects.get(map_id, {}).get("status", "not_supplied"))
        self.cache.put(key, result)
        return result

    @lru_cache(maxsize=50000)
    def pair(self, a_key: str, b_key: str, scenario_id: str) -> dict:
        a, b = self.by_key[a_key], self.by_key[b_key]
        scenario = next(s for s in self.config["scenarios"] if s["id"] == scenario_id)
        endpoint = self.config.get("endpoint", "penetration")
        totals = {"mean": 0., "lower": 0., "upper": 0., "coverage": 0., "first_fire": 0.}
        missing = set(); trace = set();timing_trace=[]
        for sa, wa in self.skills:
            for sb, wb in self.enemy_skills:
                weight = wa * wb
                pa, pb = self.shot(a, b, scenario, sa), self.shot(b, a, scenario, sb)
                ta = weapon_timing(a["vehicle"], a["ammo"] or {}, sa, scenario, self.config["timing"])
                tb = weapon_timing(b["vehicle"], b["ammo"] or {}, sb, scenario, self.config["timing"])
                timing_trace.append({'skill_a':sa,'skill_b':sb,'weight':weight,'a':ta,'b':tb})
                if pa is None or pb is None or not ta["valid"] or not tb["valid"]:
                    missing.add("one_or_both_directions_or_timing_missing")
                    totals["mean"] += weight * .5; totals["upper"] += weight
                    continue
                trace.update((pa['aim_cache_key'],pb['aim_cache_key']))
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
                    if pa['unknown_mass']==0 and pb['unknown_mass']==0:
                        lower=upper=outcome['score']
                    else:
                        lower = duel(ta, tb, [0, 0, pa["lower"]], [0, 0, pb["upper"]], scenario["horizon_s"], self.config["timing"])["score"]
                        upper = duel(ta, tb, [0, 0, pa["upper"]], [0, 0, pb["lower"]], scenario["horizon_s"], self.config["timing"])["score"]
                totals["mean"] += weight * outcome["score"]
                totals["lower"] += weight * lower; totals["upper"] += weight * upper
                unknown_a = pa["lethality"]["unknown_mass"] if endpoint == "damage" else pa["unknown_mass"]
                unknown_b = pb["lethality"]["unknown_mass"] if endpoint == "damage" else pb["unknown_mass"]
                totals["coverage"] += weight * min(1 - unknown_a, 1 - unknown_b)
                totals["first_fire"] += weight * outcome["first_fire_probability"][0]
        result={**totals, "score": totals["mean"] if totals["coverage"] > 0 else None,
                "endpoint": endpoint, "missing": sorted(missing), "calibrated": False,
                "shot_keys":sorted(trace), "pair_key":digest([a_key,b_key,scenario,sorted(trace),timing_trace,totals,self.config]),
                "estimate_kind":"conditional_model_with_declared_unknown_prior", "observed_mean":None}
        self.cache.evidence_put(result['pair_key'],'pair',{**result,'attacker_key':a_key,'target_key':b_key,'scenario':scenario,'timing':timing_trace})
        return result

    def rooms(self, nation: str, cap: int) -> list[tuple[int, float]]:
        spread = self.config["matchmaking"]["spread_steps"]
        max_br = max((v["br_step"] for v in self.variants), default=cap)
        tops = list(range(cap, min(max_br, cap + spread) + 1))
        return [(top, 1 / len(tops)) for top in tops]

    @lru_cache(maxsize=1024)
    def opponents(self, nation: str, room_top: int, domain: str) -> tuple:
        sampler=self.config['matchmaking'].get('lineup_sampler',{})
        if sampler.get('enabled'):
            mass=defaultdict(float)
            for lineup,weight in self.opponent_lineups(nation,room_top):
                members=[k for k in lineup if self.by_key[k]['domain']==domain]
                if members:
                    for k in members:mass[k]+=weight/len(members)
            total=sum(mass.values())
            return tuple((k,v/total) for k,v in sorted(mass.items())) if total else ()
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

    @lru_cache(maxsize=1024)
    def opponent_lineups(self,nation,room_top):
        """Seeded legal queue prior. No directory frequency is interpreted as telemetry."""
        import random
        rules=self.config['matchmaking'];c=rules['lineup_sampler']
        rng=random.Random(digest([c['seed'],nation,room_top]))
        nations=defaultdict(lambda:defaultdict(lambda:defaultdict(list)))
        for v in self.variants:
            if (not rules['same_nation_enemies'] and v['nation']==nation) or not room_top-rules['spread_steps']<=v['br_step']<=room_top:continue
            group=v['vehicle'].get('equivalence_group') if v['vehicle'].get('equivalence_verified') else v['vehicle_id']
            nations[v['nation']][group][v['vehicle_id']].append(v)
        result=defaultdict(int)
        eligible=[n for n in sorted(nations) if any(v['domain']=='ground' for group in nations[n].values() for variants in group.values() for v in variants)]
        if not eligible:return ()
        for _ in range(c['samples']):
            groups=nations[rng.choice(eligible)];names=sorted(groups);rng.shuffle(names)
            lineup=[];systems=set()
            for group in names:
                parent=rng.choice(sorted(groups[group]));v=rng.choice(sorted(groups[group][parent],key=lambda x:x['key']))
                if v.get('system_id') and v['system_id'] in systems:continue
                lineup.append(v);systems.add(v.get('system_id'))
                if len(lineup)==c['slots']:break
            if not any(v['domain']=='ground' for v in lineup):continue
            result[tuple(sorted(v['key'] for v in lineup))]+=1
        count=sum(result.values())
        return tuple((k,v/count) for k,v in sorted(result.items())) if count else ()

    @lru_cache(maxsize=50000)
    def mission(self, key: str, task: str, room_top: int, scenario_id: str) -> dict:
        v = self.by_key[key]
        if task == "ground" and v["domain"] != "ground": return {"mean": 0., "lower": 0., "upper": 0., "coverage": 1., "production_coverage":1., "usable": False}
        domain = "air" if task in ("anti_air","anti_helicopter","cap") else "ground"
        enemies = self.opponents(v["nation"], room_top, domain)
        trace=[]
        totals = {"mean": 0., "lower": 0., "upper": 0., "coverage": 0., "production_coverage":0., "usable": True}
        if not enemies: return {**totals, "mean": .5, "upper": 1.}
        for enemy, weight in enemies:
            if task == "ground":
                row = self.pair(key, enemy, scenario_id)
                trace.append(row['pair_key'])
            else:
                external = self.missions.get((key, self.by_key[enemy]["vehicle_id"], scenario_id, task))
                if external:trace.append(external['evidence_sha256']);self.cache.evidence_put(external['evidence_sha256'],'external_mission',external)
                row = {**external, "coverage": 1. if external['status']=='validated' else 0.} if external else {"mean": .5, "lower": 0., "upper": 1., "coverage": 0.}
            totals["production_coverage"]+=weight*(row["coverage"] if task=="ground" else float(bool(external and external.get("production_validated"))))
            for name in ("mean", "lower", "upper", "coverage"):
                totals[name] += weight * row[name]
        totals['mission_key']=digest([key,task,room_top,scenario_id,trace,totals])
        totals['evidence_trace']={'pair_keys':trace,'external_evidence':task!='ground'}
        self.cache.evidence_put(totals['mission_key'],'mission',{**totals,'variant_key':key,'task':task,'room_top':room_top,'scenario':scenario_id})
        return totals
