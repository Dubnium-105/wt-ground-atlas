"""Source-bound conditional member penetration intervals for multi-unit systems."""
import math
import numpy as np

from .aim import integrate, skill_nodes
from .data import digest, number
from .delivery import delivery_issue


def declared_members(catalog, system_id):
    vehicles={v['id']:v for v in catalog['vehicles']};root=vehicles.get(system_id)
    if len(vehicles)!=len(catalog['vehicles']):raise ValueError('Duplicate vehicle identity')
    if not root or root.get('kind')!='ground' or root.get('system_role')!='basic' or root.get('system_id')!=system_id:
        raise ValueError('Declared basic system vehicle required')
    source=root.get('multi_vehicle_system',{});members=source.get('members',[])
    if (source.get('basic_vehicle_id')!=system_id or not members or
        source.get('provenance',{}).get('method')!='official-Wiki-multiunit-membership' or
        source.get('provenance',{}).get('url')!=root.get('wiki_url') or not root.get('wiki_url')):
        raise ValueError('System source membership missing')
    result=[];seen=set()
    for member in members:
        identifier=member.get('vehicle_id');vehicle=vehicles.get(identifier);count=member.get('count')
        if (identifier in seen or not vehicle or vehicle.get('kind')!='ground' or type(count) is not int or count<1 or
            vehicle.get('system_id')!=system_id or vehicle.get('nation')!=root.get('nation') or
            member.get('role') not in ('basic','component') or
            (member.get('role')=='basic')!=(identifier==system_id) or
            member.get('role')!=vehicle.get('system_role') or
            vehicle.get('multi_vehicle_system',{}).get('members')!=members):
            raise ValueError('Inconsistent system member')
        seen.add(identifier);result.append({'vehicle_id':identifier,'role':member['role'],'count':count})
    if sum(m['role']=='basic' and m['vehicle_id']==system_id for m in result)!=1:
        raise ValueError('System basic member missing')
    return result


def unknown_exposure_bounds(members):
    """Bounds over arbitrary target-selection weights; never invent a mean."""
    if not members:raise ValueError('No system members')
    for item in members:
        lo,hi=item.get('lower'),item.get('upper')
        if not number(lo) or not number(hi) or not 0<=lo<=hi<=1:
            raise ValueError('Invalid member penetration interval')
    if any('by_skill' in m for m in members):
        nodes=members[0].get('by_skill',[])
        if (not nodes or any(not number(n.get('weight')) or n['weight']<0 for n in nodes) or
                not math.isclose(math.fsum(n['weight'] for n in nodes),1.,abs_tol=1e-10)):
            raise ValueError('Invalid skill distribution')
        lower_terms=[];upper_terms=[]
        for index,node in enumerate(nodes):
            choices=[]
            for member in members:
                values=member.get('by_skill',[])
                if len(values)!=len(nodes):raise ValueError('Inconsistent skill distribution')
                item=values[index]
                if (item.get('skill')!=node['skill'] or item.get('weight')!=node['weight'] or
                    not number(item.get('lower')) or not number(item.get('upper')) or
                    not 0<=item['lower']<=item['upper']<=1):raise ValueError('Invalid skill member interval')
                choices.append(item)
            lower_terms.append(node['weight']*min(item['lower'] for item in choices))
            upper_terms.append(node['weight']*max(item['upper'] for item in choices))
        total=math.fsum(node['weight'] for node in nodes)
        lower=math.fsum(lower_terms)/total;upper=math.fsum(upper_terms)/total
        return {'lower':lower,'upper':upper,'mean':None,'target_selection_weights':'unknown',
                'selection_may_depend_on_skill':True}
    return {'lower':min(m['lower'] for m in members),'upper':max(m['upper'] for m in members),
            'mean':None,'target_selection_weights':'unknown'}


def evaluate(atlas, members, bindings, attacker, ammo, profiles, scenario, config, skill_profile):
    distance=scenario['physical_distance_m'];selected=[p for p in profiles if p.get('distance_m')==distance]
    if len(selected)!=1:raise ValueError('Exactly one source profile at this distance required')
    profile=selected[0]
    if profile.get('reference_only') or delivery_issue(profile,distance):raise ValueError('Physical delivery source unavailable')
    if ammo.get('ordered_belt'):raise ValueError('Mixed-belt sequence requires its complete model')
    if not any(owned.get('id')==ammo.get('id') and digest(p)==digest(profile)
               for owned,p in atlas.bindings(attacker)):
        raise ValueError('Physical profile is not exactly owned by attacker')
    skill=config['skill_profiles'].get(skill_profile)
    if not isinstance(skill,dict) or skill.get('distribution')!='normal_assumption':raise ValueError('Unknown skill prior')
    weapon=ammo.get('weapon_profile') or attacker.get('firepower',{})
    gun=weapon.get('traverse_deg_s');stabilized=attacker.get('sensors',{}).get('stabilizer')
    stabilized=bool(stabilized) if stabilized is not None else None
    nodes=skill_nodes(skill);results=[]
    for member in members:
        target=member['vehicle_id'];map_id=atlas.map_for(target,profile['name'],distance)
        if map_id is None:
            results.append({**member,'status':'missing_map','lower':0.,'upper':1.,
                'by_skill':[{'skill':node,'weight':weight,'lower':0.,'upper':1.} for node,weight in nodes]});continue
        meta=atlas.meta[map_id];binding=bindings.get(target);prov=profile.get('provenance',{})
        if (not binding or meta.get('target_sha256')!=binding.get('target_sha256') or
            meta.get('ammo_profile_sha256')!=digest(profile) or
            (meta.get('source_version'),meta.get('datamine_commit'))!=(prov.get('client_version'),prov.get('datamine_commit'))):
            raise ValueError('System member map source mismatch')
        arrays=atlas.load(map_id);angles=np.flatnonzero(arrays['angles_deg']==scenario['yaw_deg'])
        if len(angles)!=1:raise ValueError('Exact system member yaw missing')
        i=int(angles[0]);rows=[]
        for node,weight in nodes:
            row=integrate(arrays['penetration_probability'][i],arrays['status'][i],float(arrays['pixel_pitch_m']),
                          node,scenario,config['aim'],gun_control=gun,stabilized=stabilized)
            rows.append({'skill':node,'weight':weight,'lower':row['lower'],'upper':row['upper']})
        total=math.fsum(r['weight'] for r in rows)
        lower=math.fsum(r['weight']*r['lower'] for r in rows)/total
        upper=math.fsum(r['weight']*r['upper'] for r in rows)/total
        if not math.isfinite(lower) or not math.isfinite(upper):raise ValueError('Invalid aim integration')
        results.append({**member,'status':'source_bound_map','lower':lower,'upper':upper,'by_skill':rows,
            'map_id':map_id,'array_sha256':meta['array_sha256'],'target_sha256':meta['target_sha256']})
    return {'scenario_id':scenario['id'],'physical_distance_m':distance,'yaw_deg':scenario['yaw_deg'],
            'profile_id':profile['name'],'members':results,'exposure_bounds':unknown_exposure_bounds(results),
            'interpretation':'Conditional protected-target penetration for a selected member; not system disablement, destruction, hit rate or win rate.'}
