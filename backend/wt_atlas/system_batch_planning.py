"""Plan source-owned system-member research from verified physical indexes."""
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from .common import content_hash, file_hash, read_json
from .forecast.data import Atlas, br_step, number
from .forecast.delivery import delivery_issue
from .forecast.engine import ammunition_payloads
from .forecast.inventory import bind_geometry
from .forecast.system_physics import declared_members
from .system_research_atlas import index_snapshot


def candidate_requests(catalog, atlas, config, available):
    """Plan only homogeneous selections whose complete member maps exist."""
    systems=[v for v in catalog['vehicles'] if v.get('system_role')=='basic']
    members={v['id']:declared_members(catalog,v['id']) for v in systems}
    scenarios=config['scenarios'];distances={s['physical_distance_m'] for s in scenarios}
    required={d:{s['yaw_deg'] for s in scenarios if s['physical_distance_m']==d} for d in distances}
    requests=[];keys=set()
    for attacker in catalog['vehicles']:
        rating=attacker.get('br',{}).get('current')
        if attacker.get('kind')!='ground' or attacker.get('system_role')=='component' or not number(rating):continue
        for ammo,profiles,_ in ammunition_payloads(atlas.bindings(attacker)):
            if ammo.get('ordered_belt'):continue
            chosen={}
            for distance in distances:
                exact=[p for p in profiles if p.get('distance_m')==distance]
                if len(exact)!=1 or delivery_issue(exact[0],distance):break
                chosen[distance]=exact[0]['name']
            if len(chosen)!=len(distances):continue
            for system in systems:
                system_rating=system.get('br',{}).get('current')
                if (not number(system_rating) or attacker.get('nation')==system.get('nation') or
                    abs(br_step(rating)-br_step(system_rating))>config['matchmaking']['spread_steps']):continue
                required_keys={(m['vehicle_id'],name,distance) for m in members[system['id']] for distance,name in chosen.items()}
                if not all(required[distance]<=available.get((target,name,distance),set()) for target,name,distance in required_keys):continue
                keys.update(required_keys)
                requests.append({'system_id':system['id'],'attacker_id':attacker['id'],'ammo_id':ammo['id'],
                                 'profiles':{str(d):chosen[d] for d in sorted(chosen)}})
    requests.sort(key=lambda r:(r['system_id'],r['attacker_id'],r['ammo_id']))
    if len({(r['system_id'],r['attacker_id'],r['ammo_id']) for r in requests})!=len(requests):
        raise ValueError('Duplicate research candidate identity')
    return {'requests':requests,'keys':[{'target_id':t,'profile_id':p,'distance_m':d} for t,p,d in sorted(keys)]}


def validate_plan(plan, catalog, atlas, config):
    rows=plan.get('keys',[]);keys={(r['target_id'],r['profile_id'],r['distance_m']) for r in rows}
    if len(keys)!=len(rows):raise ValueError('Duplicate planned physical identity')
    available={key:{s['yaw_deg'] for s in config['scenarios'] if s['physical_distance_m']==key[2]} for key in keys}
    replay=candidate_requests(catalog,atlas,config,available)
    if replay['requests']!=plan.get('requests') or replay['keys']!=rows:
        raise ValueError('Research plan does not replay from its owned selections')
    return keys


def plan(catalog_path, ammunition_path, config_path, bindings_path, indexes):
    sources=[Path(p).resolve() for p in (catalog_path,ammunition_path,config_path,bindings_path)]
    indexes=[Path(p).resolve() for p in indexes]
    if not indexes or len(set(indexes))!=len(indexes):raise ValueError('Missing or duplicate source index')
    root=Path(__file__).resolve().parents[2]
    code=[root/p for p in ('backend/wt_atlas/system_batch_planning.py','backend/wt_atlas/system_research_atlas.py',
        'backend/wt_atlas/forecast/engine.py','backend/wt_atlas/forecast/data.py','backend/wt_atlas/forecast/belts.py',
        'backend/wt_atlas/forecast/delivery.py','backend/wt_atlas/forecast/system_physics.py','backend/wt_atlas/forecast/inventory.py',
        'backend/wt_atlas/npz_io.py','backend/wt_atlas/common.py','scripts/plan_system_research_batch.py')]
    def code_hash(path):return hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    code_hashes={p.relative_to(root).as_posix():code_hash(p) for p in code}
    hashes={str(p):file_hash(p) for p in sources};snapshots={str(p):index_snapshot(p) for p in indexes}
    catalog=read_json(sources[0]);config=read_json(sources[2]);receipt=read_json(sources[3])
    targets={v['id'] for v in catalog['vehicles'] if v.get('system_role') in ('basic','component')}
    selected={**catalog,'vehicles':[v for v in catalog['vehicles'] if v['id'] in targets]}
    def geometry():
        bound,errors=bind_geometry(Path(receipt['repository']),Path(receipt['jobs']),selected)
        if errors or set(bound)!=targets or any(receipt.get('bindings',{}).get(k,{}).get('target_sha256')!=v['target_sha256'] for k,v in bound.items()):
            raise ValueError('System geometry source verification failed')
        return bound
    bound=geometry()
    class EmptyIndex:metadata={};lookup={}
    atlas=Atlas(sources[0].parent,sources[1],catalog,index=EmptyIndex());available={};receipts=[]
    for path in indexes:
        count=0
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute("SELECT count(*) FROM maps WHERE selected=1 AND status!='verified'").fetchone()[0]:
                raise ValueError('Source index has unverified selections')
            for target in sorted(targets):
                for ammo,distance,angles,raw in db.execute("SELECT ammo,distance,angles,metadata FROM maps WHERE target=? AND selected=1 AND status='verified'",(target,)):
                    profile=atlas.profiles.get(ammo);meta=json.loads(raw);provenance=(profile or {}).get('provenance',{});key=(target,ammo,distance)
                    if key in available:raise ValueError('Duplicate physical identity across source indexes')
                    if (profile is None or profile.get('reference_only') or profile.get('distance_m')!=distance or
                        meta.get('target_id')!=target or meta.get('ammo_id')!=ammo or
                        meta.get('target_binding')!='independent_geometry_verified' or
                        meta.get('target_sha256')!=bound[target]['target_sha256'] or
                        meta.get('ammo_profile_sha256')!=content_hash(profile) or
                        (meta.get('source_version'),meta.get('datamine_commit'))!=(provenance.get('client_version'),provenance.get('datamine_commit'))):
                        raise ValueError('Source index conflicts with system inputs')
                    values=json.loads(angles or '[]')
                    if not isinstance(values,list) or any(not number(v) for v in values):raise ValueError('Invalid source angle coverage')
                    available[key]=set(values);count+=1
        receipts.append({'index_sha256':snapshots[str(path)],'selected_system_maps':count})
    result=candidate_requests(catalog,atlas,config,available)
    if (any(file_hash(p)!=hashes[str(p)] for p in sources) or any(index_snapshot(p)!=snapshots[str(p)] for p in indexes) or geometry()!=bound or
            any(code_hash(p)!=code_hashes[p.relative_to(root).as_posix()] for p in code)):
        raise ValueError('Research planning inputs changed')
    result.update(schema_version='1.0.0',input_sha256=hashes,source_indexes=receipts,
        code_sha256_lf=code_hashes,
        scope='Source-owned homogeneous ammunition and complete indexed member maps; base BR and nation planning only. No dynamic BR, unlock, hit, damage, system-disablement or ranking claim.',
        source_arrays_rechecked=False,publishable=False,formal_rankings=0)
    return result
