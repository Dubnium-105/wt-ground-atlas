"""Plan real target maps for members of declared multi-vehicle systems.

The parent's base BR bounds a planning scope; it is not a system damage model,
an observed target distribution, or proof of dynamic configuration eligibility.
"""
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .common import content_hash, file_hash, read_json
from .forecast.data import Atlas, br_step, number, safe_file
from .forecast.delivery import delivery_issue
from .forecast.inventory import bind_geometry, stamp


def component_requests(catalog, profiles_by_attacker, scenarios, spread_steps, existing=None):
    vehicles={v['id']:v for v in catalog['vehicles']}
    if len(vehicles)!=len(catalog['vehicles']):raise ValueError('Duplicate vehicle identity')
    if type(spread_steps) is not int or spread_steps<0:raise ValueError('Invalid matchmaking spread')
    by_distance=defaultdict(set)
    for scenario in scenarios:
        distance,yaw=scenario.get('physical_distance_m'),scenario.get('yaw_deg')
        if not number(distance) or distance<0 or not number(yaw):raise ValueError('Invalid physical scenario')
        by_distance[distance].add(yaw)
    if not by_distance:raise ValueError('No physical scenarios')
    existing=existing or {};counts=Counter();requests=defaultdict(set);yaws=defaultdict(set);targets=[]
    for component in sorted(vehicles.values(),key=lambda v:v['id']):
        if component.get('system_role')!='component':continue
        parent=vehicles.get(component.get('system_id'));system=component.get('multi_vehicle_system',{})
        parent_system=(parent or {}).get('multi_vehicle_system',{})
        members=system.get('members',[])
        member=[m for m in members if m.get('vehicle_id')==component['id'] and m.get('role')=='component']
        if (not parent or component.get('kind')!='ground' or parent.get('kind')!='ground' or
            parent.get('system_role')!='basic' or parent.get('system_id')!=parent['id'] or
            parent.get('nation')!=component.get('nation') or system.get('basic_vehicle_id')!=parent['id'] or
            parent_system.get('basic_vehicle_id')!=parent['id'] or parent_system.get('members')!=members or
            any(not isinstance(vehicle.get('wiki_url'),str) or not vehicle['wiki_url'] or
                source.get('provenance',{}).get('method')!='official-Wiki-multiunit-membership' or
                source.get('provenance',{}).get('url')!=vehicle.get('wiki_url')
                for source,vehicle in ((system,component),(parent_system,parent))) or
            len(member)!=1 or type(member[0].get('count')) is not int or member[0]['count']<1 or
            not number(parent.get('br',{}).get('current'))):
            raise ValueError('Invalid source system membership or parent BR: '+component['id'])
        target_br=br_step(parent['br']['current']);targets.append({'vehicle_id':component['id'],
            'parent_id':parent['id'],'parent_base_br':parent['br']['current'],'source_member_count':member[0]['count']})
        for attacker in vehicles.values():
            br=attacker.get('br',{}).get('current')
            if (attacker.get('kind')!='ground' or attacker.get('system_role')=='component' or
                attacker.get('nation')==component.get('nation') or not number(br) or
                abs(br_step(br)-target_br)>spread_steps):continue
            for profile in profiles_by_attacker.get(attacker['id'],()):
                distance=profile.get('distance_m');name=profile.get('name')
                if (profile.get('mode') not in ('KE','CE') or profile.get('reference_only') or
                    distance not in by_distance or not isinstance(name,str) or not name):continue
                if delivery_issue(profile,distance):
                    counts['attacker_profiles_outside_declared_delivery_bounds']+=1;continue
                missing=by_distance[distance]-existing.get((component['id'],name,distance),set())
                if not missing:continue
                key=(component['id'],name);requests[key].add(attacker['id']);yaws[key].update(missing)
    return {'schema_version':'1.0.0','target_components':targets,'candidate_request_count':len(requests),
        'counts':dict(counts),'candidate_rebuild_requests':[{'target_vehicle_id':target,'profile_id':profile,
            'attacker_ids':sorted(owners),'required_yaws_deg':sorted(yaws[(target,profile)])}
            for (target,profile),owners in sorted(requests.items())],
        'scope':'Parent base BR and nation only; components are physical targets, not independent lineup choices. No dynamic BR, system outcome, target distribution, or flight probability is inferred.',
        'publishable':False,'formal_rankings':0}


def plan(catalog_path, ammunition_path, config_path, bindings_path, indexes=()):
    inputs={'catalog':Path(catalog_path),'ammunition':Path(ammunition_path),
            'config':Path(config_path),'bindings':Path(bindings_path)}
    hashes={name:file_hash(path) for name,path in inputs.items()}
    catalog=read_json(catalog_path);config=read_json(config_path)
    class EmptyIndex:metadata={};lookup={}
    atlas=Atlas(Path(catalog_path).parent,Path(ammunition_path),catalog,index=EmptyIndex())
    attackers={v['id']:{p['name']:p for _,p in atlas.bindings(v)}.values() for v in catalog['vehicles']
               if v.get('kind')=='ground' and v.get('system_role')!='component'}
    targets={v['id'] for v in catalog['vehicles'] if v.get('system_role')=='component'};existing={};receipts=[]
    report=read_json(bindings_path); selected={**catalog,'vehicles':[v for v in catalog['vehicles'] if v['id'] in targets]}
    bound,errors=bind_geometry(Path(report['repository']),Path(report['jobs']),selected)
    if errors or set(bound)!=targets:raise ValueError('Component geometry source verification failed')
    if any(report.get('bindings',{}).get(k,{}).get('target_sha256')!=v['target_sha256'] for k,v in bound.items()):
        raise ValueError('Component geometry receipt no longer matches source')
    for path in indexes:
        path=Path(path);before=file_hash(path)
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute("SELECT count(*) FROM maps WHERE selected=1 AND status!='verified'").fetchone()[0]:
                raise ValueError('Input index has unverified selected arrays')
            root_row=db.execute("SELECT value FROM settings WHERE key='root'").fetchone()
            if not root_row:raise ValueError('Input index has no source root')
            root=Path(root_row[0])
            for target,ammo,distance,angles,raw,array_stamp,meta_hash,path_name in db.execute(
                    "SELECT target,ammo,distance,angles,metadata,array_stamp,meta_hash,path FROM maps WHERE selected=1 AND status='verified'"):
                if target not in targets:continue
                meta=json.loads(raw);profile=atlas.profiles.get(ammo); provenance=(profile or {}).get('provenance',{})
                metadata_file=safe_file(root,'arrays/'+path_name)
                array_file=safe_file(root,meta['array_file'])
                if (profile is None or profile.get('reference_only') or profile.get('distance_m')!=distance or
                    meta.get('target_id')!=target or meta.get('ammo_id')!=ammo or
                    meta.get('target_sha256')!=bound[target]['target_sha256'] or
                    meta.get('target_binding')!='independent_geometry_verified' or
                    meta.get('ammo_profile_sha256')!=content_hash(profile) or
                    (meta.get('source_version'),meta.get('datamine_commit'))!=
                    (provenance.get('client_version'),provenance.get('datamine_commit')) or
                    file_hash(metadata_file)!=meta_hash or stamp(array_file)!=array_stamp or
                    file_hash(array_file)!=meta.get('array_sha256')):
                    raise ValueError('Existing component map identity or source changed')
                existing.setdefault((target,ammo,distance),set()).update(json.loads(angles or '[]'))
        if file_hash(path)!=before:raise ValueError('Input index changed during planning')
        receipts.append({'sha256':before})
    result=component_requests(catalog,attackers,config['scenarios'],config['matchmaking']['spread_steps'],existing)
    if any(file_hash(path)!=hashes[name] for name,path in inputs.items()):raise ValueError('Planning source changed')
    result.update(catalog_sha256=hashes['catalog'],ammunition_sha256=hashes['ammunition'],
                  config_sha256=hashes['config'],bindings_sha256=hashes['bindings'],source_indexes=receipts)
    return result
