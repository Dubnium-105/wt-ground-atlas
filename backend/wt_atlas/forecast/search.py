"""Resource-constrained sortie policy and multi-start beam / two-slot exchange search."""
from __future__ import annotations

import itertools
import math
from functools import lru_cache

from .data import number


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


def sortie_policy(rows: list[list[dict]], costs: list[int | None], config: dict) -> dict:
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
    return dict(zip(("mean", "lower", "upper", "coverage", "first_action"), value), states=visits,
                status=config["status"], probability_model="declared_exogenous_point_gains")


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
                costs = []
                for v in lineup:
                    explicit = (v.get("loadout") or {}).get("spawn_cost", v["vehicle"].get("spawn_cost"))
                    if explicit is None:
                        explicit = self.config["spawn"]["ground_cost" if v["domain"] == "ground" else "air_cost"]
                    if explicit is not None and (not number(explicit) or explicit < 0 or int(explicit) != explicit):
                        raise ValueError("Invalid sourced spawn cost")
                    costs.append(None if explicit is None else int(explicit))
                result = sortie_policy(rows, costs, self.config["spawn"])
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
        return {"keys": list(keys), "members": members, "nation": nation,
                "actual_br": round(cap / self.config["matchmaking"]["br_step_denominator"], 1),
                "score": 100 * estimate if eligible else None, "estimate": 100 * estimate,
                "lower": 100 * aggregate("lower"), "upper": 100 * aggregate("upper"),
                "evidence_coverage": coverage, "rankable": eligible,
                "status": "uncalibrated_conditional_prediction" if eligible else "insufficient_paired_evidence",
                "cases": cases, "endpoint": self.config.get("endpoint", "penetration"),
                "global_optimum_guaranteed": False}

    def _rank(self, keys: tuple[str, ...]):
        result = self.evaluate(keys)
        return (-int(result["rankable"]), -result["estimate"], -result["evidence_coverage"], len(keys), keys)

    def discover(self, nation: str, cap: int, slots: int, acquisition: str) -> dict:
        if acquisition not in ("researchable", "all"): raise ValueError("Unknown acquisition profile")
        pool = [v for v in self.e.variants if v["nation"] == nation and v["br_step"] <= cap and
                (acquisition == "all" or (v["vehicle"].get("special") is not True and v["vehicle"].get("acquisition", {}).get("researchable") is not False))]
        by_key = {v["key"]: v for v in pool}
        all_keys = sorted(by_key)
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
                "budget_exhausted": exhausted, "algorithm": "multi_start_beam_and_one_two_slot_exchange",
                "global_optimum_guaranteed": False}

    def world(self) -> dict:
        tables = []
        for nation in sorted({v["nation"] for v in self.e.variants}):
            caps = sorted({v["br_step"] for v in self.e.variants if v["nation"] == nation})
            for slots in self.config["search"]["slots"]:
                for acquisition in self.config["search"]["acquisition_profiles"]:
                    for cap in caps:
                        tables.append(self.discover(nation, cap, slots, acquisition))
        boards = {}
        for slots in self.config["search"]["slots"]:
            for acquisition in self.config["search"]["acquisition_profiles"]:
                rows = [r for t in tables if t["slots"] == slots and t["acquisition"] == acquisition for r in t["candidates"] if r["rankable"]]
                rows.sort(key=lambda r: (-r["score"], -r["evidence_coverage"], r["keys"]))
                boards[f"{slots}/{acquisition}"] = [{**r, "rank": i + 1} for i, r in enumerate(rows[:self.config["search"]["global_limit"]])]
        return {"boards": boards, "tables": tables, "evaluations": sum(t["evaluations"] for t in tables),
                "truncated_searches": sum(t["budget_exhausted"] for t in tables), "global_optimum_guaranteed": False}
