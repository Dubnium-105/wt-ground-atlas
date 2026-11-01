"""Cached system-member intervals with the same semantics as system_physics."""
from collections import OrderedDict
import copy
import math
import numpy as np

from .aim import integrate, regions, skill_nodes
from .data import digest
from .delivery import delivery_issue
from .system_physics import unknown_exposure_bounds


class AimIntervalCache:
    """Bounded LRU of exact aim intervals and map-specific weakpoint regions."""
    def __init__(self, aim_config, max_entries=100000):
        if type(max_entries) is not int or max_entries<1:raise ValueError('Invalid aim cache limit')
        self.aim=copy.deepcopy(aim_config);self.limit=max_entries
        self.intervals=OrderedDict();self.prepared=OrderedDict();self.hits=0;self.calculations=0

    def interval(self, map_id, arrays, index, skill, scenario, gun, stabilized):
        key=(map_id,index,skill,digest(scenario),gun,stabilized)
        if key in self.intervals:
            self.intervals.move_to_end(key);self.hits+=1;return self.intervals[key]
        rkey=(map_id,index)
        if rkey in self.prepared:
            self.prepared.move_to_end(rkey);prepared=self.prepared[rkey]
        else:
            prepared=regions(arrays['penetration_probability'][index],arrays['status'][index],float(arrays['pixel_pitch_m']),self.aim)
            self.prepared[rkey]=prepared
            if len(self.prepared)>self.limit:self.prepared.popitem(last=False)
        row=integrate(arrays['penetration_probability'][index],arrays['status'][index],float(arrays['pixel_pitch_m']),
                      skill,scenario,self.aim,gun_control=gun,stabilized=stabilized,prepared=prepared)
        result=(row['lower'],row['upper']);self.intervals[key]=result;self.calculations+=1
        if len(self.intervals)>self.limit:self.intervals.popitem(last=False)
        return result


def evaluate_cached(atlas, members, bindings, attacker, ammo, profiles, scenario, config, skill_profile, cache):
    distance=scenario['physical_distance_m'];selected=[p for p in profiles if p.get('distance_m')==distance]
    if len(selected)!=1:raise ValueError('Exactly one source profile at this distance required')
    profile=selected[0]
    if profile.get('reference_only') or delivery_issue(profile,distance):raise ValueError('Physical delivery source unavailable')
    if ammo.get('ordered_belt'):raise ValueError('Mixed-belt sequence requires its complete model')
    if not any(owned.get('id')==ammo.get('id') and digest(p)==digest(profile) for owned,p in atlas.bindings(attacker)):
        raise ValueError('Physical profile is not exactly owned by attacker')
    skill=config['skill_profiles'].get(skill_profile)
    if not isinstance(skill,dict) or skill.get('distribution')!='normal_assumption':raise ValueError('Unknown skill prior')
    weapon=ammo.get('weapon_profile') or attacker.get('firepower',{});gun=weapon.get('traverse_deg_s')
    stabilized=attacker.get('sensors',{}).get('stabilizer');stabilized=bool(stabilized) if stabilized is not None else None
    nodes=skill_nodes(skill);results=[]
    for member in members:
        target=member['vehicle_id'];map_id=atlas.map_for(target,profile['name'],distance)
        if map_id is None:
            results.append({**member,'status':'missing_map','lower':0.,'upper':1.,
                'by_skill':[{'skill':node,'weight':weight,'lower':0.,'upper':1.} for node,weight in nodes]});continue
        meta=atlas.meta[map_id];binding=bindings.get(target);prov=profile.get('provenance',{})
        if (not binding or meta.get('target_sha256')!=binding.get('target_sha256') or meta.get('ammo_profile_sha256')!=digest(profile) or
            (meta.get('source_version'),meta.get('datamine_commit'))!=(prov.get('client_version'),prov.get('datamine_commit'))):
            raise ValueError('System member map source mismatch')
        arrays=atlas.load(map_id);angles=np.flatnonzero(arrays['angles_deg']==scenario['yaw_deg'])
        if len(angles)!=1:raise ValueError('Exact system member yaw missing')
        rows=[]
        for node,weight in nodes:
            lower,upper=cache.interval(map_id,arrays,int(angles[0]),node,scenario,gun,stabilized)
            rows.append({'skill':node,'weight':weight,'lower':lower,'upper':upper})
        total=math.fsum(r['weight'] for r in rows)
        lower=math.fsum(r['weight']*r['lower'] for r in rows)/total;upper=math.fsum(r['weight']*r['upper'] for r in rows)/total
        if not math.isfinite(lower) or not math.isfinite(upper):raise ValueError('Invalid aim integration')
        results.append({**member,'status':'source_bound_map','lower':lower,'upper':upper,'by_skill':rows,
                        'map_id':map_id,'array_sha256':meta['array_sha256'],'target_sha256':meta['target_sha256']})
    return {'scenario_id':scenario['id'],'physical_distance_m':distance,'yaw_deg':scenario['yaw_deg'],
            'profile_id':profile['name'],'members':results,'exposure_bounds':unknown_exposure_bounds(results),
            'interpretation':'Conditional protected-target penetration for a selected member; not system disablement, destruction, hit rate or win rate.'}
