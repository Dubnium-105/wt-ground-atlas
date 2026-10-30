"""Two-sided timed event generator. Penetration and damage endpoints stay distinct."""
from __future__ import annotations

import math
import numpy as np

from .data import number


def weapon_timing(vehicle: dict, ammo: dict, skill: float, scenario: dict, c: dict) -> dict:
    """Durations, not attribute-score bonuses; missing timing evidence remains visible."""
    if not number(skill) or any(not number(scenario[k]) or scenario[k]<0 for k in ('approach_m','hull_offset_deg','cover_distance_m','horizon_s')):raise ValueError('Invalid timing input')
    if vehicle.get("forecast_timing_unverified"):
        return {"valid": False, "missing": ["ambiguous_patched_weapon_timing"]}
    weapon = ammo.get("weapon_profile") or vehicle.get("firepower", {})
    missing = []
    typ = weapon.get("reload_type")
    if typ == "manual":
        reload = weapon.get("reload_ace_s")
    elif typ == "autoloader":
        reload = weapon.get("autoload_s")
    elif typ == "automatic":
        rpm = weapon.get("fire_rate_rpm")
        reload = 60.0 / rpm if number(rpm) and rpm > 0 else weapon.get("autoload_s")
    else:
        reload = None  # A missile launch interval is not its reload time.
    if not number(reload) or reload <= 0:
        return {"valid": False, "missing": ["weapon_specific_reload"]}
    traverse = weapon.get("traverse_deg_s")
    if not number(traverse) or traverse <= 0:
        traverse = c["traverse_fallback_deg_s"]; missing.append("traverse")
    thermal = vehicle.get("sensors", {}).get("thermal_generation")
    if not number(thermal): thermal = 0; missing.append("sensor_detection_model")
    mobility = vehicle.get("mobility", {})
    speed = mobility.get("forward_kmh")
    if not number(speed) or speed <= 0:
        speed = c["missing_speed_m_s"]; missing.append("forward_speed")
    else:
        speed = speed / 3.6 * c["terrain_speed_fraction"]
    power = mobility.get("power_to_weight_hp_t")
    acceleration = power * c["acceleration_per_hp_t"] if number(power) and power > 0 else c["acceleration_fallback_m_s2"]
    if not number(power): missing.append("acceleration_prior")
    distance = scenario["approach_m"]
    accelerate_distance = speed * speed / (2 * acceleration)
    arrival = math.sqrt(2 * distance / acceleration) if distance <= accelerate_distance else speed / acceleration + (distance - accelerate_distance) / speed
    acquire = c["acquire_s"] * math.exp(-c["acquire_skill"] * skill) / (1 + c["sensor_gain"] * thermal)
    slew = abs(scenario["hull_offset_deg"]) / traverse
    aim = c["aim_s"] * math.exp(-c["aim_skill"] * skill)
    first = arrival + acquire + slew + aim
    cycle = max(reload, c["reaim_s"] * math.exp(-c["aim_skill"] * skill))
    # Automatic weapons fire within one tracking burst at their real cadence.
    if typ == "automatic": cycle = reload
    reverse = mobility.get("reverse_kmh")
    exposure = scenario["horizon_s"]
    if scenario["cover_distance_m"] > 0:
        if number(reverse) and reverse > 0:
            exposure = min(scenario["horizon_s"], first + scenario["cover_distance_m"] / (reverse / 3.6))
        else: missing.append("retreat_speed")
    ready = weapon.get("ready_rounds") or weapon.get("belt_capacity") or weapon.get("first_order_rounds")
    total = weapon.get("total_ammunition")
    capacity = min([int(x) for x in (ready, total) if number(x) and x >= 1], default=None)
    # Unverified ready-rack replenishment is not invented; stop at the known rack.
    return {"valid": True, "first_s": first, "cycle_s": cycle, "exposure_end_s": exposure,
            "ready_capacity": capacity, "arrival_s": arrival, "acquire_s": acquire,
            "aim_s": aim, "slew_s": slew, "reload_s": reload,
            "missing": missing, "status": "uncalibrated_timing_generator"}


def _matrix(q: list[float], endpoint: str) -> np.ndarray:
    """Rows = target states, columns = next states. 0/1/2/3 = F/M bitmask; 4 = terminal."""
    if len(q) != 3 or not all(number(x) and 0 <= x <= 1 for x in q) or sum(q) > 1 + 1e-10:
        raise ValueError("Invalid event outcome probabilities")
    matrix = np.zeros((5, 5))
    for state in range(4):
        matrix[state, state] += 1 - sum(q)
        matrix[state, state | 1] += q[0]
        matrix[state, state | 2] += q[1]
        matrix[state, 4] += q[2]
    matrix[4, 4] = 1.0
    return matrix


def duel(a: dict, b: dict, qa: list[float], qb: list[float], horizon: float, c: dict,
         *, endpoint: str = "penetration") -> dict:
    """Exact finite-state propagation under declared independent-shot assumptions.

    For penetration mode, qa=[0,0,E_shot] is a terminal protected-target event,
    NOT destruction. For damage mode the supplied post-penetration kernel is used.
    Simultaneous shots use pre-event shooter states, avoiding order advantage.
    """
    if endpoint not in ("penetration", "damage"): raise ValueError("Unsupported endpoint")
    if not number(horizon) or horizon<=0:raise ValueError('Invalid horizon')
    if not a.get("valid") or not b.get("valid"):
        return {"score": None, "reason": "missing_weapon_timing"}
    ma, mb = _matrix(qa, endpoint), _matrix(qb, endpoint)
    events = []
    for side, t in enumerate((a, b)):
        if any(not number(t[k]) or t[k]<0 for k in ('cycle_s','first_s','exposure_end_s')):raise ValueError('Invalid event timing')
        if t['ready_capacity'] is not None and (not isinstance(t['ready_capacity'],int) or isinstance(t['ready_capacity'],bool) or t['ready_capacity']<1):raise ValueError('Invalid ammunition capacity')
        if t["cycle_s"] <= 0: raise ValueError("Invalid cadence")
        count = max(0, math.floor((horizon - t["first_s"]) / t["cycle_s"]) + 1)
        if t["ready_capacity"] is not None: count = min(count, t["ready_capacity"])
        if count > c["max_events"]: raise ValueError("Event budget exceeded; do not silently cap automatic fire")
        events.extend((t["first_s"] + k * t["cycle_s"], side) for k in range(count))
    events.sort()
    if endpoint == 'penetration' and qa[:2] == [0,0] and qb[:2] == [0,0]:
        # Absorbing first-event model: vectorized survival has the same simultaneous
        # semantics as the joint-state propagation, at O(events) rather than 25
        # matrix transitions per event. Damage still uses the full state model.
        groups=[];i=0
        while i<len(events):
            t=events[i][0];sides=set()
            while i<len(events) and abs(events[i][0]-t)<=c['simultaneous_tolerance_s']:
                sides.add(events[i][1]);i+=1
            if t<=a['exposure_end_s'] and t<=b['exposure_end_s']:groups.append((0 in sides,1 in sides))
        fire=np.asarray(groups,dtype=float).reshape((-1,2))
        pa=fire[:,0]*qa[2];pb=fire[:,1]*qb[2]
        survival=(1-pa)*(1-pb)
        prior=np.concatenate(([1.],np.cumprod(survival)))
        wins=float(np.sum(prior[:-1]*pa*(1-pb)));losses=float(np.sum(prior[:-1]*pb*(1-pa)))
        ties=float(np.sum(prior[:-1]*pa*pb));pending=float(prior[-1])
        state=np.zeros((5,5));state[0,4]=wins;state[4,0]=losses;state[4,4]=ties;state[0,0]=pending
        first=(fire[0]/fire[0].sum()).tolist() if len(fire) else [0.,0.]
        return {'score':wins+.5*(ties+pending),'a_only':wins,'b_only':losses,'both':ties,'unresolved':pending,
            'expected_shots':(prior[:-1,None]*fire).sum(axis=0).tolist(),'first_fire_probability':first,
            'state_mass':state.tolist(),'endpoint':endpoint,'kill_probability':None,
            'assumptions':['conditional independent shots','fixed per-shot map while firing','no repair during horizon']}
    state = np.zeros((5, 5)); state[0, 0] = 1.0
    shots = [0.0, 0.0]
    i = 0
    first_probability = [0.0, 0.0]
    if events:
        t0 = events[0][0]
        first_sides = {side for time, side in events if abs(time - t0) <= c["simultaneous_tolerance_s"]}
        for side in first_sides: first_probability[side] = 1 / len(first_sides)
    while i < len(events):
        time = events[i][0]; sides = set()
        while i < len(events) and abs(events[i][0] - time) <= c["simultaneous_tolerance_s"]:
            sides.add(events[i][1]); i += 1
        updated = np.zeros_like(state)
        for sa, sb in zip(*np.nonzero(state)):
            probability = state[sa, sb]
            # Immobilized targets cannot execute their planned retreat.
            exposed_a = time <= a["exposure_end_s"] or (sa < 4 and bool(sa & 2))
            exposed_b = time <= b["exposure_end_s"] or (sb < 4 and bool(sb & 2))
            fire_a = 0 in sides and sa < 4 and not sa & 1 and sb < 4 and exposed_a and exposed_b
            fire_b = 1 in sides and sb < 4 and not sb & 1 and sa < 4 and exposed_a and exposed_b
            target_a = mb[sa] if fire_b else np.eye(5)[sa]
            target_b = ma[sb] if fire_a else np.eye(5)[sb]
            updated += probability * np.outer(target_a, target_b)
            shots[0] += probability * fire_a; shots[1] += probability * fire_b
        state = updated
    if not np.isclose(state.sum(), 1, atol=1e-10): raise ArithmeticError("Duel state mass drift")
    # In damage mode, a gun-disabled target is also a mission denial, not a kill.
    lost = [4] if endpoint == "penetration" else [1, 3, 4]
    alive = [x for x in range(5) if x not in lost]
    wins = float(state[np.ix_(alive, lost)].sum())
    losses = float(state[np.ix_(lost, alive)].sum())
    ties = float(state[np.ix_(lost, lost)].sum())
    pending = float(state[np.ix_(alive, alive)].sum())
    return {"score": wins + 0.5 * (ties + pending), "a_only": wins, "b_only": losses,
            "both": ties, "unresolved": pending, "expected_shots": shots,
            "first_fire_probability": first_probability, "state_mass": state.tolist(),
            "endpoint": endpoint, "kill_probability": float(state[:, 4].sum()) if endpoint == "damage" else None,
            "assumptions": ["conditional independent shots", "fixed per-shot map while firing", "no repair during horizon"]}
