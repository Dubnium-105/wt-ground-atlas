"""Resource-constrained sortie policy and multi-start beam / two-slot exchange search."""
from __future__ import annotations

import itertools
import math
from functools import lru_cache

from .data import number
from .data import digest, canonical
from .capabilities import spawn_cost


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


def sortie_policy(rows: list[list[dict]], costs: list[int | None], config: dict, *, domains=None, cost_function=None) -> dict:
    """Optimize expected phase rewards under point gains, one sortie per vehicle.

    The point transition is exogenous and labelled in config; utility is never
    interpreted as the probability of earning SP. Bounds follow the SAME policy.
    """
    weights, gains = config["phase_weights"], config["point_gains"]
    if len(rows) != len(weights) or any(len(r) != len(costs) for r in rows): raise ValueError("Policy shape mismatch")
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
                previous=sum(bool(used & (1<<j)) for j in range(len(costs)) if domains[j]==domains[i])
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
    active={(0,config['initial_points']):1.};actions=[]
    for phase in range(len(rows)):
        following={};occupancy={}
        for (used,points),mass in active.items():
            chosen=solve(phase,used,points)[4]
            occupancy[chosen]=occupancy.get(chosen,0.)+mass
            if chosen is None:
                following[(used,points)]=following.get((used,points),0.)+mass;continue
            cost=costs[chosen]
            if cost_function:
                previous=sum(bool(used & (1<<j)) for j in range(len(costs)) if domains[j]==domains[chosen])
                cost=cost_function(chosen,previous)
            for gain in gains:
                state=(used | (1<<chosen),min(config['max_points'],points-cost+gain['points']))
                following[state]=following.get(state,0.)+mass*gain['probability']
        actions.append({'phase':phase,'actions':[{'member_index':i,'probability':p} for i,p in occupancy.items()]})
        active=following
    return dict(zip(("mean", "lower", "upper", "coverage", "first_action"), value), states=visits,
                status=config["status"], probability_model="declared_exogenous_point_gains",policy=actions)


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
        if not feasible(lineup, max(self.config["search"]["slots"])): raise ValueError("Illegal lineup")
        cap = max(v["br_step"] for v in lineup)
        nation = lineup[0]["nation"]
        cases = []
        for room, room_weight in self.e.rooms(nation, cap):
            for scenario in self.config["scenarios"]:
                phases = self.config["spawn"]["phases"]
                rows = [[self.e.mission(v["key"], task, room, scenario["id"]) for v in lineup] for task in phases]
                costs = [spawn_cost(v,self.config['spawn'],room) for v in lineup]
                result = sortie_policy(rows, costs, self.config["spawn"], domains=[v['domain'] for v in lineup],
                    cost_function=lambda i,n:spawn_cost(lineup[i],self.config['spawn'],room,n))
                result.update(weight=room_weight * scenario["weight"], room_top_step=room, scenario=scenario["id"])
                cases.append(result)
        risk = self.config["search"]["risk_aversion"]
        def aggregate(key):
            expected = sum(r["weight"] * r[key] for r in cases)
            tail = lower_tail([(r[key], r["weight"]) for r in cases], self.config["search"]["tail_mass"])
            return (1 - risk) * expected + risk * tail
        coverage = sum(r["weight"] * r["coverage"] for r in cases)
        eligible = coverage >= self.config["search"]["minimum_pair_coverage"]
        estimate = aggregate("mean")
        members = [{k: v[k] for k in ("vehicle_id", "rating_configuration_id", "ammo_id", "loadout_id", "key", "br", "classification")} for v in lineup]
        for m,v in zip(members,lineup):
            m.update(name=v['vehicle'].get('name',v['vehicle_id']),mode='ground_rb',version=v.get('version'),
                     loadout=v.get('loadout'),ammo_profile_sha256=digest(v['profile']) if v.get('profile') else None,
                     source=v['vehicle'].get('source_url'),taxonomy_original={'kind':v['vehicle'].get('kind'),'role':v['vehicle'].get('role')})
        return {"keys": list(keys), "members": members, "nation": nation,
                "actual_br": round(cap / self.config["matchmaking"]["br_step_denominator"], 1),
                "score": 100 * estimate if eligible else None, "estimate": 100 * estimate,
                "lower": 100 * aggregate("lower"), "upper": 100 * aggregate("upper"),
                "evidence_coverage": coverage, "rankable": eligible,
                "status": "uncalibrated_conditional_prediction" if eligible else "insufficient_paired_evidence",
                "cases": cases, "endpoint": self.config.get("endpoint", "penetration"),
                "lineup_key":digest([keys,self.config]),
                "uncertainty":{"physical_missing_interval":[100*aggregate('lower'),100*aggregate('upper')],
                    "model_error":None,"out_of_sample_confidence_interval":None},
                "mission_coverage":{task:sum(r['weight']*sum(a['probability']*(a['member_index'] is not None) for a in r['policy'][i]['actions']) for r in cases) for i,task in enumerate(self.config['spawn']['phases'])},
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

    def _rank(self, keys: tuple[str, ...]):
        result = self.evaluate(keys)
        return (-int(result["rankable"]), -result["estimate"], -result["evidence_coverage"], len(keys), keys)

    def discover(self, nation: str, cap: int, slots: int, acquisition: str) -> dict:
        if acquisition not in ("researchable", "all"): raise ValueError("Unknown acquisition profile")
        pool = [v for v in self.e.variants if v["nation"] == nation and v["br_step"] <= cap and
                (acquisition == "all" or (v["vehicle"].get("special") is not True and v["vehicle"].get("acquisition", {}).get("researchable") is not False))]
        pool,pruned=self.pareto(pool,cap)
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
        results = []
        # Include valid visited sets; a local-search step must not discard a better seed.
        for keys in sorted(visited, key=self._rank):
            if max(by_key[k]["br_step"] for k in keys) != cap: continue
            result = self.evaluate(keys)
            parents = {m["vehicle_id"] for m in result["members"]}
            if any(len(parents & prior) / len(parents | prior) >= self.config["search"]["diversity_overlap"] for prior, _ in results): continue
            results.append((parents, result))
            if len(results) == self.config["search"]["per_cap_limit"]: break
        return {"nation": nation, "cap_step": cap, "slots": slots, "acquisition": acquisition,
                "candidates": [r for _, r in results], "evaluations": len(visited),
                "pareto_payloads_removed":pruned,"seed":self.config['search'].get('seed',0),
                "budget_exhausted": exhausted, "algorithm": "multi_start_beam_and_one_two_slot_exchange",
                "global_optimum_guaranteed": False}

    def world(self) -> dict:
        tables = []
        for nation in sorted({v["nation"] for v in self.e.variants}):
            if self.config['search'].get('nations') and nation not in self.config['search']['nations']:continue
            caps = sorted({v["br_step"] for v in self.e.variants if v["nation"] == nation})
            limits=self.config['search'].get('br_range')
            if limits:caps=[c for c in caps if limits[0]<=round(c/3,1)<=limits[1]]
            for slots in self.config["search"]["slots"]:
                for acquisition in self.config["search"]["acquisition_profiles"]:
                    for cap in caps:
                        tables.append(self.discover(nation, cap, slots, acquisition))
        boards = {}
        for slots in self.config["search"]["slots"]:
            for acquisition in self.config["search"]["acquisition_profiles"]:
                rows = [r for t in tables if t["slots"] == slots and t["acquisition"] == acquisition and not t['budget_exhausted'] for r in t["candidates"] if r["rankable"]]
                rows.sort(key=lambda r: (-r["score"], -r["evidence_coverage"], r["keys"]))
                seen=set();unique=[]
                for row in rows:
                    signature=tuple(sorted(m['vehicle_id'] for m in row['members']))
                    if signature in seen:continue
                    seen.add(signature);unique.append(row)
                rows=unique
                boards[f"{slots}/{acquisition}"] = [{**r, "rank": i + 1} for i, r in enumerate(rows[:self.config["search"]["global_limit"]])]
        return {"boards": boards, "tables": tables, "evaluations": sum(t["evaluations"] for t in tables),
                "truncated_searches": sum(t["budget_exhausted"] for t in tables), "global_optimum_guaranteed": False}
