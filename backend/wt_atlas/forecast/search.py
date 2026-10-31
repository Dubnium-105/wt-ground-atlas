"""Resource-constrained sortie policy and multi-start beam / two-slot exchange search."""
from __future__ import annotations

import itertools
import math
from functools import lru_cache

from .data import number
from .data import digest, canonical
from .capabilities import spawn_cost, spawn_group
from .rankings import build_views, select


def lower_tail(values: list[tuple[float, float]], mass: float) -> float:
    """Weighted lower-tail CVaR; split the boundary atom, not an unweighted quantile."""
    if not 0 < mass <= 1 or not values or any(not number(v) or not number(w) or w < 0 for v, w in values):
        raise ValueError("Invalid weighted distribution")
    total = sum(w for _, w in values)
    if total <= 0: raise ValueError("No distribution mass")
    remaining, result = mass, 0.
    for value, weight in sorted(values):
        taken = min(remaining, weight / total)
        result += value * taken; remaining -= taken
        if remaining <= 1e-15: break
    return result / mass


def sortie_policy(rows: list[list[dict]], costs: list[int | None], config: dict, *, domains=None, spawn_groups=None, cost_function=None) -> dict:
    """Optimize expected phase rewards under point gains, one sortie per vehicle.

    The point transition is exogenous and labelled in config; utility is never
    interpreted as the probability of earning SP. Bounds follow the SAME policy.
    """
    weights, gains = config["phase_weights"], config["point_gains"]
    if len(rows) != len(weights) or any(len(r) != len(costs) for r in rows): raise ValueError("Policy shape mismatch")
    groups=spawn_groups if spawn_groups is not None else domains
    if cost_function and (groups is None or len(groups)!=len(costs)):
        raise ValueError('Spawn category shape mismatch')
    for cost in costs:
        if cost is not None and (not isinstance(cost, int) or isinstance(cost, bool) or cost < 0):
            raise ValueError("Spawn costs must be known nonnegative integer points")
    visits = 0

    @lru_cache(maxsize=None)
    def solve(phase, used, points):
        nonlocal visits
        visits += 1
        if visits > config["max_states"]: raise ValueError("Sortie state budget exceeded")
        if phase == len(rows): return (0., 0., 0., 0., None)
        # Waiting does not consume a vehicle or earn an invented SP bonus.
        tail = solve(phase + 1, used, points)
        best = (tail[0], tail[1], tail[2], tail[3] + weights[phase], None)
        for i, cost in enumerate(costs):
            item = rows[phase][i]
            if cost_function:
                previous=sum(bool(used & (1<<j)) for j in range(len(costs)) if groups[j]==groups[i])
                cost=cost_function(i,previous)
            if used & (1 << i) or cost is None or cost > points or not item.get("usable", True): continue
            expected = [weights[phase] * item[k] for k in ("mean", "lower", "upper", "coverage")]
            for gain in gains:
                after = min(config["max_points"], points - cost + gain["points"])
                future = solve(phase + 1, used | (1 << i), after)
                for j in range(4): expected[j] += gain["probability"] * future[j]
            choice = (*expected, i)
            if (choice[0], choice[3], -(i + 1)) > (best[0], best[3], -(best[4] + 1) if best[4] is not None else -len(costs)-1):
                best = choice
        return best
    value = solve(0, 0, config["initial_points"])
    # Policy occupancy is marginalized over the declared SP transitions.
    active={(0,config['initial_points']):1.};actions=[];evidence_coverage=0.;assumed_coverage=0.
    for phase in range(len(rows)):
        following={};occupancy={}
        for (used,points),mass in active.items():
            chosen=solve(phase,used,points)[4]
            occupancy[chosen]=occupancy.get(chosen,0.)+mass
            if chosen is None:
                following[(used,points)]=following.get((used,points),0.)+mass;continue
            evidence_coverage+=mass*weights[phase]*rows[phase][chosen]['coverage']
            assumed_coverage+=mass*weights[phase]*rows[phase][chosen].get('assumed_coverage',0.)
            cost=costs[chosen]
            if cost_function:
                previous=sum(bool(used & (1<<j)) for j in range(len(costs)) if groups[j]==groups[chosen])
                cost=cost_function(chosen,previous)
            for gain in gains:
                state=(used | (1<<chosen),min(config['max_points'],points-cost+gain['points']))
                following[state]=following.get(state,0.)+mass*gain['probability']
        actions.append({'phase':phase,'actions':[{'member_index':i,'probability':p} for i,p in occupancy.items()]})
        active=following
    return dict(zip(("mean", "lower", "upper", "coverage", "first_action"), value), states=visits,
                status=config["status"], probability_model="declared_exogenous_point_gains",policy=actions,
                evidence_coverage=evidence_coverage,assumed_coverage=assumed_coverage)


def feasible(lineup: tuple[dict, ...], slots: int) -> bool:
    if not lineup or len(lineup) > slots or len({v["nation"] for v in lineup}) != 1: return False
    if not any(v["domain"] == "ground" for v in lineup): return False
    if len({v["vehicle_id"] for v in lineup}) != len(lineup): return False
    systems = [v["system_id"] for v in lineup if v.get("system_id")]
    return len(systems) == len(set(systems))


class Optimizer:
    def __init__(self, engine):
        self.e = engine
        self.config = engine.config
        self.evaluated = 0
        self.truncated = False

    @lru_cache(maxsize=50000)
    def evaluate(self, keys: tuple[str, ...]) -> dict:
        lineup = tuple(self.e.by_key[k] for k in keys)
        if any(not v.get('selection_ready',True) for v in lineup):raise ValueError('Unknown owned configuration cannot be selected')
        if not feasible(lineup, max(self.config["search"]["slots"])): raise ValueError("Illegal lineup")
        cap = max(v["br_step"] for v in lineup)
        nation = lineup[0]["nation"]
        cases = []
        for room, room_weight in self.e.rooms(nation, cap):
            for scenario in self.config["scenarios"]:
                phases = self.config["spawn"]["phases"]
                rows = [[self.e.mission(v["key"], task, room, scenario["id"]) for v in lineup] for task in phases]
                costs = [spawn_cost(v,self.config['spawn'],room) for v in lineup]
                result = sortie_policy(rows, costs, self.config["spawn"], spawn_groups=[spawn_group(v) for v in lineup],
                    cost_function=lambda i,n:spawn_cost(lineup[i],self.config['spawn'],room,n))
                result['task_evidence']=[sum(a['probability']*(rows[i][a['member_index']]['coverage'] if a['member_index'] is not None else 0.) for a in phase['actions']) for i,phase in enumerate(result['policy'])]
                result['mission_keys']=[[r.get('mission_key') for r in phase] for phase in rows]
                result['production_mission_coverage']={task:min(r.get('production_coverage',0.) for r in rows[i]) for i,task in enumerate(phases)}
                # Defaults may supply task utility, never missing ground physics.
                if result['assumed_coverage'] > 0:
                    result['physical_pair_coverage'] = min(
                        (self.e.mission(v['key'],'ground',room,scenario['id'])['coverage']
                         for v in lineup if v['domain']=='ground'), default=0.)
                else:
                    result['physical_pair_coverage'] = result['evidence_coverage']
                result.update(weight=room_weight * scenario["weight"], room_top_step=room, scenario=scenario["id"])
                cases.append(result)
        risk = self.config["search"]["risk_aversion"]
        def aggregate(key):
            expected = sum(r["weight"] * r[key] for r in cases)
            tail = lower_tail([(r[key], r["weight"]) for r in cases], self.config["search"]["tail_mass"])
            return (1 - risk) * expected + risk * tail
        coverage = sum(r["weight"] * r["evidence_coverage"] for r in cases)
        assumed = sum(r['weight'] * r['assumed_coverage'] for r in cases)
        physical = sum(r['weight'] * r['physical_pair_coverage'] for r in cases)
        eligible = min(coverage + assumed, physical) >= self.config["search"]["minimum_pair_coverage"]
        estimate = aggregate("mean")
        members = [{k: v[k] for k in ("vehicle_id", "rating_configuration_id", "ammo_id", "loadout_id", "key", "br", "classification")} for v in lineup]
        for m,v in zip(members,lineup):
            m.update(name=v['vehicle'].get('name',v['vehicle_id']),mode='ground_rb',version=v.get('version'),
                     loadout=v.get('loadout'),ammo_profile_sha256=digest(v['profile']) if v.get('profile') else None,
                     ammo_profiles_by_distance=[{'distance_m':p['distance_m'],'profile_id':p['name'],'sha256':digest(p)} for p in v.get('profiles',())],
                     source=v['vehicle'].get('source_url'),taxonomy_original={'kind':v['vehicle'].get('kind'),'role':v['vehicle'].get('role')})
        return {"keys": list(keys), "members": members, "lineup_size": len(members), "nation": nation,
                "actual_br": round(cap / self.config["matchmaking"]["br_step_denominator"], 1),
                "score": 100 * estimate if eligible else None, "estimate": 100 * estimate,
                "lower": 100 * aggregate("lower"), "upper": 100 * aggregate("upper"),
                "evidence_coverage": coverage, "rankable": eligible,
                "assumed_mission_coverage":assumed, "physical_pair_coverage":physical,
                "mission_policy":self.config.get('mission_policy',{'mode':'require_evidence'}),
                "model_state_coverage":sum(r["weight"]*r["coverage"] for r in cases),
                "production_mission_coverage":{task:sum(r["weight"]*r["production_mission_coverage"][task] for r in cases) for task in set(self.config["spawn"]["phases"])},
                "status": ("assumed_mission_prediction" if assumed else "uncalibrated_conditional_prediction") if eligible else "insufficient_paired_evidence",
                "cases": cases, "endpoint": self.config.get("endpoint", "penetration"),
                "composition": "ground_only" if all(v["domain"] == "ground" for v in lineup) else "combined_arms",
                "uptier_lower_utility": 100 * min((c["lower"] for c in cases if c["room_top_step"] > cap), default=min(c["lower"] for c in cases)),
                "lineup_key":digest([getattr(self.e,'build_key',None),keys,self.config,cases]),
                "uncertainty":{"physical_missing_interval":[100*aggregate('lower'),100*aggregate('upper')],
                    "includes_mission_assumptions":assumed>0,
                    "model_error":None,"out_of_sample_confidence_interval":None},
                "mission_coverage":{task:sum(r['weight']*r['task_evidence'][i] for r in cases) for i,task in enumerate(self.config['spawn']['phases'])},
                "global_optimum_guaranteed": False}

    def pareto(self,pool,cap):
        """Prune dominated payloads of ONE parent only; never remove backup vehicles.

        A dominance decision needs complete evidence for every task/context. Missing
        intervals and unknown costs cannot justify pruning an alternative.
        """
        if not self.config['search'].get('pareto',False):return pool,0
        groups={}
        for v in pool:groups.setdefault((v['vehicle_id'],v['br_step']),[]).append(v)
        kept=[];removed=0
        for group in groups.values():
            if len(group)==1:kept+=group;continue
            vectors={};costs={}
            for v in group:
                rows=[self.e.mission(v['key'],task,room,s['id']) for room,_ in self.e.rooms(v['nation'],cap) for s in self.config['scenarios'] for task in set(self.config['spawn']['phases'])]
                vectors[v['key']]=[r['mean'] for r in rows] if all(r['coverage']>=1-1e-12 for r in rows) else None
                costs[v['key']]=spawn_cost(v,self.config['spawn'],cap)
            for v in group:
                x=vectors[v['key']];cost=costs[v['key']]
                dominated=False
                if x is not None and cost is not None:
                    for other in group:
                        y=vectors[other['key']];oc=costs[other['key']]
                        if y is not None and oc is not None and oc<=cost and all(b>=a for a,b in zip(x,y)) and (any(b>a for a,b in zip(x,y)) or oc<cost):dominated=True;break
                if dominated:removed+=1
                else:kept.append(v)
        return kept,removed

    def explain(self,result):
        """Remove each member in the SAME rooms; this is conditional reserve value."""
        lineup=[self.e.by_key[k] for k in result['keys']];contributions=[]
        for removed,v in enumerate(lineup):
            without=0.;use=0.
            for case in result['cases']:
                rows=[[self.e.mission(k,task,case['room_top_step'],case['scenario']) for k in result['keys']] for task in self.config['spawn']['phases']]
                costs=[None if i==removed else spawn_cost(x,self.config['spawn'],case['room_top_step']) for i,x in enumerate(lineup)]
                policy=sortie_policy(rows,costs,self.config['spawn'],spawn_groups=[spawn_group(x) for x in lineup],
                    cost_function=lambda i,n:None if i==removed else spawn_cost(lineup[i],self.config['spawn'],case['room_top_step'],n))
                without+=case['weight']*policy['mean']
                use+=case['weight']*sum(a['probability'] for phase in case['policy'] for a in phase['actions'] if a['member_index']==removed)
            baseline=sum(c['weight']*c['mean'] for c in result['cases'])
            contributions.append({'vehicle_id':v['vehicle_id'],'sortie_probability':use,'marginal_expected_utility_same_rooms':100*(baseline-without)})
        result['reserve_contributions']=contributions
        result['environment_threats']=sorted([{'room_top_step':c['room_top_step'],'scenario':c['scenario'],'lower':c['lower'],'upper':c['upper'],'coverage':c['coverage']} for c in result['cases']],key=lambda c:c['lower'])
        self.e.cache.evidence_put(result['lineup_key'],'lineup',result)
        return result

    def _rank(self, keys: tuple[str, ...]):
        result = self.evaluate(keys)
        return (-int(result["rankable"]), -result["estimate"], -result["evidence_coverage"], -len(keys), keys)

    def discover(self, nation: str, cap: int, slots: int, acquisition: str) -> dict:
        if acquisition not in ("researchable", "all"): raise ValueError("Unknown acquisition profile")
        pool = [v for v in self.e.variants if v.get('selection_ready',True) and v["nation"] == nation and v["br_step"] <= cap and
                (acquisition == "all" or (v["vehicle"].get("special") is not True and v["vehicle"].get("acquisition", {}).get("researchable") is not False))]
        pool,pruned=self.pareto(pool,cap)
        available = len({('system', v['system_id']) if v.get('system_id') else ('vehicle', v['vehicle_id']) for v in pool})
        if available < slots:
            return {"nation": nation, "cap_step": cap, "slots": slots, "acquisition": acquisition,
                    "candidates": [], "category_candidates": {}, "evaluations": 0,
                    "available_parent_groups": available, "candidate_status": "insufficient_distinct_members",
                    "pareto_payloads_removed": pruned, "seed": self.config['search'].get('seed', 0),
                    "budget_exhausted": False, "algorithm": "multi_start_beam_and_one_two_slot_exchange",
                    "global_optimum_guaranteed": False}

        by_key = {v["key"]: v for v in pool}
        all_keys = sorted(by_key,key=lambda k:digest([self.config['search'].get('seed',0),k]))
        ground = [v for v in pool if v["domain"] == "ground"]
        anchors = [v for v in pool if v["br_step"] == cap]
        budget, visited, exhausted = self.config["search"]["max_evaluations"], set(), False

        def accept(keys):
            nonlocal exhausted
            keys = tuple(sorted(keys))
            if keys in visited: return True
            if not feasible(tuple(by_key[k] for k in keys), slots) or max(by_key[k]["br_step"] for k in keys) != cap: return False
            if len(visited) >= budget: exhausted = True; return False
            self.evaluate(keys); visited.add(keys)
            return True

        seeds = set()
        for anchor in anchors:
            for g in ([anchor] if anchor["domain"] == "ground" else ground):
                keys = tuple(sorted({anchor["key"], g["key"]}))
                if accept(keys): seeds.add(keys)
        beam = sorted(seeds, key=self._rank)[:self.config["search"]["beam_width"]]
        for _ in range(slots):
            expanded = set(beam)
            for current in beam:
                if len(current) >= slots: continue
                for k in all_keys:
                    if k in current: continue
                    new = tuple(sorted((*current, k)))
                    if accept(new): expanded.add(new)
                    if exhausted: break
                if exhausted: break
            next_beam = sorted(expanded, key=self._rank)[:self.config["search"]["beam_width"]]
            if next_beam == beam or exhausted: break
            beam = next_beam
        # Two-slot exchanges allow a coupled ground/air or BR-profile change.
        width = self.config["search"]["exchange_width"]
        for _ in range(self.config["search"]["local_passes"]):
            expanded = set(beam)
            for current in beam[:width]:
                external = []
                for k in all_keys:
                    if k in current: continue
                    merit = []
                    for removed in current:
                        trial = tuple(sorted((set(current) - {removed}) | {k}))
                        if accept(trial): merit.append(self._rank(trial))
                        if exhausted: break
                    if merit: external.append((min(merit), k))
                    if exhausted: break
                replacements = [k for _, k in sorted(external)[:width]]
                for count in (1, 2):
                    for removed in itertools.combinations(current, count):
                        retained = set(current) - set(removed)
                        for added in itertools.combinations(replacements, count):
                            new = tuple(sorted(retained | set(added)))
                            if new and max(by_key[k]["br_step"] for k in new) == cap and accept(new): expanded.add(new)
                            if exhausted: break
                        if exhausted: break
                    if exhausted: break
                if exhausted: break
            next_beam = sorted(expanded, key=self._rank)[:self.config["search"]["beam_width"]]
            if next_beam == beam or exhausted: break
            beam = next_beam
        # Retain category alternatives before the ordinary per-cap shortlist.
        # All selections are actual evaluated lineups, never assembled member scores.
        evaluated = [self.evaluate(keys) for keys in visited if len(keys) == slots]
        choices = {view: select(evaluated, view, limit=self.config['search']['per_cap_limit'],
                               diversity=self.config['search']['diversity_overlap'], rankable_only=False)
                   for view in ('global', 'ground_only', 'combined_arms', 'uptier')}
        explained = {}
        for rows in choices.values():
            for row in rows:
                if row['lineup_key'] not in explained:
                    explained[row['lineup_key']] = self.explain(row)
        return {"nation": nation, "cap_step": cap, "slots": slots, "acquisition": acquisition,
                "candidates": choices["global"], "category_candidates": {k: v for k, v in choices.items() if k != "global"},
                "evaluations": len(visited), "available_parent_groups": available,
                "candidate_status": "search_truncated" if exhausted else "complete_lineups" if evaluated else "no_feasible_complete_lineup",
                "pareto_payloads_removed":pruned,"seed":self.config['search'].get('seed',0),
                "budget_exhausted": exhausted, "algorithm": "multi_start_beam_and_one_two_slot_exchange",
                "global_optimum_guaranteed": False}

    def world(self) -> dict:
        tables = [];restored_tables=0;new_evaluations=0
        for nation in sorted({v["nation"] for v in self.e.variants}):
            if self.config['search'].get('nations') and nation not in self.config['search']['nations']:continue
            caps = sorted({v["br_step"] for v in self.e.variants if v["nation"] == nation})
            limits=self.config['search'].get('br_range')
            if limits:caps=[c for c in caps if limits[0]<=round(c/3,1)<=limits[1]]
            for slots in self.config["search"]["slots"]:
                for acquisition in self.config["search"]["acquisition_profiles"]:
                    for cap in caps:
                        key=digest([getattr(self.e,'build_key',None),nation,cap,slots,acquisition,self.config])
                        cached=self.e.cache.derived_get(key) if getattr(self.e,'resume',False) and getattr(self.e,'build_key',None) else None
                        if cached is not None:
                            tables.append(cached);restored_tables+=1
                        else:
                            table=self.discover(nation, cap, slots, acquisition);tables.append(table)
                            new_evaluations+=table["evaluations"]
                            if getattr(self.e,'build_key',None):self.e.cache.derived_put(key,table)
        views = build_views(tables, self.config)
        return {"boards": views["global"], "views": views, "tables": tables, "evaluations": sum(t["evaluations"] for t in tables),
                "execution":{"restored_tables":restored_tables,"computed_tables":len(tables)-restored_tables,"new_evaluations":new_evaluations,"restored_evaluations":sum(t["evaluations"] for t in tables)-new_evaluations},
                "truncated_searches": sum(t["budget_exhausted"] for t in tables), "global_optimum_guaranteed": False}
