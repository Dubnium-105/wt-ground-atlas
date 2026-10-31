"""Recompute explicit missing native pairs from original ordered geometry.

The CPU implementation and optional unchanged V2 CUDA implementation have
separate model identities. No existing probability file is an input.
"""
from collections import defaultdict,Counter
import json
import os
from pathlib import Path
import sys
import sqlite3
import tempfile
import time
import zipfile
import numpy as np
from .common import read_json,write_json,file_hash,content_hash
from .cpu_model import PreparedLayers
from .forecast.data import Atlas,number,safe_file,br_step
from .forecast.inventory import bind_geometry,verify_array
from .forecast.resources import usage

ROOT=Path(__file__).resolve().parents[2]
VENDOR=ROOT/'backend/vendor/grp_probability_v2'
MARKER='.wt-native-rebuild'


def plan(catalog_path,ammunition_path,index_path,config_path):
    """Plan exact missing pairs against a verified inventory, without opening NPZ.

    A plan never establishes new coverage. Rebuild repeats source checks and
    rejects a changed catalog or ammunition bundle before computing anything.
    """
    catalog=read_json(catalog_path);config=read_json(config_path)
    class EmptyIndex:
        metadata={};lookup={}
    atlas=Atlas(Path(index_path).parent,ammunition_path,catalog,index=EmptyIndex())
    grounds=[v for v in catalog['vehicles'] if v.get('kind')=='ground' and number(v.get('br',{}).get('current')) and v.get('system_role')!='component']
    owned={v['id']:{p['name']:p for _,p in atlas.bindings(v)} for v in grounds}
    db=sqlite3.connect(Path(index_path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        if db.execute("SELECT count(*) FROM maps WHERE selected=1 AND status!='verified'").fetchone()[0]:
            raise ValueError('Finish inventory array verification before planning')
        available={(t,p,d):set(json.loads(angles or '[]')) for t,p,d,angles in db.execute("SELECT target,ammo,distance,angles FROM maps WHERE selected=1 AND status='verified'")}
    finally:db.close()
    counts=Counter();requests=defaultdict(set);required_yaws=defaultdict(set);missing=[]
    for attacker in grounds:
        profiles=owned[attacker['id']]
        if not profiles:missing.append(attacker['id'])
        for target in grounds:
            if attacker['nation']==target['nation'] or abs(br_step(attacker['br']['current'])-br_step(target['br']['current']))>config['matchmaking']['spread_steps']:continue
            for scenario in config['scenarios']:
                distance,yaw=scenario['physical_distance_m'],scenario['yaw_deg']
                native=[p for p in profiles.values() if p.get('distance_m')==distance and p.get('impact_distance_m') is None]
                if any(yaw in available.get((target['id'],p['name'],distance),set()) for p in native):continue
                if not native:counts['directions_without_exact_owned_native_profile']+=1;continue
                counts['directions_with_owned_profile_but_without_map']+=1
                for p in native:
                    key=(target['id'],p['name']);requests[key].add(attacker['id']);required_yaws[key].add(yaw)
    return {'schema_version':'1.0.0','catalog_sha256':file_hash(catalog_path),'ammunition_sha256':file_hash(ammunition_path),
        'config_sha256':file_hash(config_path),'inventory':str(Path(index_path).resolve()),'existing_verified_maps':len(available),
        'counts':dict(counts),'vehicles_without_exact_owned_profile':missing,'candidate_request_count':len(requests),
        'candidate_rebuild_requests':[{'target_vehicle_id':t,'profile_id':p,'attacker_ids':sorted(ids),'required_yaws_deg':sorted(required_yaws[(t,p)])} for (t,p),ids in sorted(requests.items())],
        'scope':'Configured native distances/yaws only; plan is not computed or published coverage; fresh independent geometry/version validation is required.'}


def rebuild(repository,catalog_path,ammunition_path,request_path,output,*,backend='cpu',
            config_path=None,resume=False,limit=None,memory_budget=1536*1024**2):
    started=time.perf_counter();repository=Path(repository).resolve();output=Path(output)
    if backend not in ('cpu','v2-cuda'):raise ValueError('Unsupported native backend')
    if limit is not None and (not isinstance(limit,int) or isinstance(limit,bool) or limit<1):raise ValueError('Invalid map limit')
    if memory_budget<32*1024**2:raise ValueError('Invalid memory budget')
    if any(p.is_symlink() for p in (output,*output.parents)):raise ValueError('Symlink output')
    output=output.resolve()
    if any((output/name).is_symlink() for name in (MARKER,'arrays','ammunition.json')):raise ValueError('Symlink generated path')
    if output.is_relative_to(repository) or repository.is_relative_to(output):raise ValueError('Keep generated maps outside the source repository')
    if any(Path(p).resolve().is_relative_to(output) for p in (catalog_path,ammunition_path,request_path,config_path) if p):
        raise ValueError('Native output overlaps an input')
    if output.exists() and any(output.iterdir()) and not (output/MARKER).is_file():raise ValueError('Unmanaged native output')
    catalog=read_json(catalog_path);request=read_json(request_path)
    if request.get('catalog_sha256')!=file_hash(catalog_path) or request.get('ammunition_sha256')!=file_hash(ammunition_path):
        raise ValueError('Rebuild request input hash mismatch')
    raw=read_json(ammunition_path);profiles={p['name']:p for p in raw}
    if len(profiles)!=len(raw):raise ValueError('Duplicate profile')
    config=read_json(config_path or ROOT/'config/pipeline.json')
    by_target=defaultdict(set);seen=set();required_yaws={}
    # Binding uses exact owned IDs; platform, caliber or names are not aliases.
    class EmptyIndex:
        metadata={};lookup={}
    atlas=Atlas(output,ammunition_path,catalog,index=EmptyIndex())
    vehicles={v['id']:v for v in catalog['vehicles']}
    owned={v['id']:{p['name'] for _,p in atlas.bindings(v)} for v in catalog['vehicles']}
    for row in request.get('candidate_rebuild_requests',[]):
        target,name=row['target_vehicle_id'],row['profile_id'];key=(target,name)
        if key in seen:raise ValueError('Duplicate requested physical pair')
        seen.add(key)
        if target not in vehicles or name not in profiles:raise ValueError('Unknown requested target/profile')
        p=profiles[name];provenance=p.get('provenance',{})
        if (p.get('mode') not in ('KE','CE') or p.get('reference_only') or p.get('impact_distance_m') is not None or
            not number(p.get('distance_m')) or p['distance_m']<0):raise ValueError('Original explicit native-distance profile required')
        curve=p.get('normalized_distance_curve',[])
        if p['mode']=='KE' and curve and curve[0]!=[p['distance_m'],1.]:raise ValueError('Native distance disagrees with reference source curve')
        yaws=row.get('required_yaws_deg',[])
        if not isinstance(yaws,list) or any(not number(yaw) or not 0<=yaw<360 for yaw in yaws):raise ValueError('Invalid requested yaw')
        required_yaws[key]=set(yaws)
        if not provenance.get('client_version') or not provenance.get('datamine_commit'):raise ValueError('Unversioned profile')
        if not row.get('attacker_ids') or any(name not in owned.get(v,set()) for v in row['attacker_ids']):
            raise ValueError('Requested ammunition is not exactly owned by attacker')
        by_target[target].add(name)
    if not seen:raise ValueError('No rebuild pairs')
    versions={(profiles[n]['provenance']['client_version'],profiles[n]['provenance']['datamine_commit']) for _,n in seen}
    if len(versions)!=1:raise ValueError('Use one physical source version per output')
    sys.path.insert(0,str(VENDOR))
    from grp_probability import load_ammunition
    load_ammunition(ammunition_path,{name for _,name in seen},len(read_json(VENDOR/'data/mechanisms.json')))
    selected={**catalog,'vehicles':[vehicles[t] for t in sorted(by_target)]}
    bindings,errors=bind_geometry(repository,repository/'data/work/client/jobs',selected)
    missing=sorted(set(by_target)-bindings.keys())
    if missing or errors:raise ValueError('Independent geometry unavailable: '+str(missing or errors))
    for target,names in by_target.items():
        identity=bindings[target]['identity']
        for name in names:
            p=profiles[name]['provenance']
            if not required_yaws[(target,name)]<=set(identity['plan']['angles_deg']):raise ValueError('Exact source yaw unavailable; rebuild geometry first')
            if (p['client_version'],p['datamine_commit'])!=(identity['client_version'],identity['datamine_commit']):
                raise ValueError('Geometry/profile version mismatch')
    code={p.relative_to(VENDOR).as_posix():file_hash(p) for p in sorted(VENDOR.rglob('*.py'))}
    model={'backend':backend,'tool_manifest_sha256':file_hash(VENDOR/'MANIFEST.json'),'tool_code_sha256':content_hash(code)}
    if backend=='cpu':model.update(cpu_code_sha256=file_hash(Path(__file__).with_name('cpu_model.py')),numerics_sha256=content_hash(config['numerics']))
    identity={'model':model,'versions':[list(v) for v in sorted(versions)],
              'adapter_sha256':file_hash(__file__)}
    if (output/MARKER).exists() and read_json(output/MARKER)!=identity:raise ValueError('Native output model/version changed; use a new directory')
    output.mkdir(parents=True,exist_ok=True);(output/'arrays').mkdir(exist_ok=True);write_json(output/MARKER,identity)
    bundle=output/'ammunition.json';previous={p['name']:p for p in read_json(bundle)} if bundle.exists() else {}
    for _,name in seen:
        if name in previous and previous[name]!=profiles[name]:raise ValueError('Existing output profile changed')
        previous[name]=profiles[name]
    write_json(bundle,[previous[k] for k in sorted(previous)])
    if backend=='v2-cuda':
        sys.path.insert(0,str(VENDOR/'lib'))
        from wt_armor.layered_penetration_v2 import PreparedVehicle
    report={'status':'completed','planned_maps':len(seen),'computed_maps':0,'reused_maps':0,'maps':[],
            'model':model,'input_catalog_sha256':file_hash(catalog_path),'input_ammunition_sha256':file_hash(ammunition_path),
            'requests_sha256':file_hash(request_path),'source_probability_maps_read':0,'raw_sources_modified':False,
            'endpoint':'conditional_penetration','game_probability_validated':False}
    for target,names in sorted(by_target.items()):
        bound=bindings[target]
        job=read_json(safe_file(repository/'data/work/client/jobs',target+'.json'))
        metadata=safe_file(repository,job['layer_metadata'].replace('\\','/'))
        target_profile=read_json(safe_file(repository,job['profile_path'].replace('\\','/')))
        center=target_profile.get('view_group',{}).get('center_xz_m')
        with zipfile.ZipFile(metadata.with_suffix('.npz')) as archive:decoded=sum(x.file_size for x in archive.infolist())
        if decoded>memory_budget:raise ValueError('Decoded geometry exceeds memory budget')
        prepared=None
        for name in sorted(names):
            p=profiles[name];ph=content_hash(p)
            seed={'target':bound['target_sha256'],'ammo':ph,'tool_manifest':model['tool_manifest_sha256'],'tool_code':model['tool_code_sha256']}
            if backend=='cpu':seed['cpu_model']=model
            map_id=content_hash(seed);array=output/'arrays'/(map_id+'.npz');metafile=array.with_suffix('.json')
            common={'map_id':map_id,'target_id':target,'target_sha256':bound['target_sha256'],'ammo_id':name,
                    'ammo_profile_sha256':ph,'source_version':p['provenance']['client_version'],
                    'datamine_commit':p['provenance']['datamine_commit'],'array_file':'arrays/'+array.name,
                    'tool_manifest_sha256':model['tool_manifest_sha256'],'tool_code_sha256':model['tool_code_sha256']}
            if backend=='cpu':common['cpu_model']=model
            saved=read_json(metafile) if resume and metafile.is_file() and array.is_file() else None
            valid=bool(saved and all(saved.get(k)==v for k,v in common.items()) and verify_array((str(output),saved,memory_budget))['status']=='verified')
            if valid:report['reused_maps']+=1
            else:
                if limit is not None and report['computed_maps']>=limit:continue
                if prepared is None:
                    # Explicit allow_pickle=False and structural validation occur
                    # before the unchanged optional CUDA loader reads these layers.
                    prepared=PreparedLayers(metadata,config)
                    if backend=='v2-cuda':
                        del prepared
                        prepared=PreparedVehicle(metadata)
                if backend=='cpu':probability,charge,status,flags=prepared.calculate(p)
                else:
                    probability,charge,status=prepared.calculate(p);flags=prepared.risk_flags(p)
                plan=prepared.plan
                fd,temporary=tempfile.mkstemp(prefix=map_id+'.',suffix='.part',dir=array.parent)
                try:
                    with os.fdopen(fd,'wb') as handle:
                        np.savez_compressed(handle,penetration_probability=probability,model_required_charge_mm=charge,
                            status=status,material_response_flags=flags,angles_deg=np.asarray(plan['angles_deg'],np.float64),
                            **({'center_xz_m':np.asarray(center,np.float64)} if center is not None else {}),
                            **{k:np.float64(plan[k]) for k in ('pixel_pitch_m','world_width_m','top_y_m','bottom_y_m')})
                    os.replace(temporary,array)
                finally:
                    if Path(temporary).exists():Path(temporary).unlink()
                saved={**common,'array_sha256':file_hash(array),'status_counts':{str(i):int(np.count_nonzero(status==i)) for i in range(4)}}
                check=verify_array((str(output),saved,memory_budget))
                if check['status']!='verified':raise ValueError('Generated NPZ failed validation: '+str(check))
                write_json(metafile,saved);report['computed_maps']+=1
            report['maps'].append({'map_id':map_id,'target':target,'profile':name,'distance_m':p['distance_m'],'array_sha256':saved['array_sha256'],'reused':valid})
        if prepared is not None and backend=='v2-cuda':
            cp=prepared.cp;del prepared;cp.get_default_memory_pool().free_all_blocks()
    report.update(pending_maps=len(seen)-len(report['maps']),duration_seconds=time.perf_counter()-started,resources=usage())
    if report['pending_maps']:report['status']='partial'
    write_json(output/'run.json',report)
    return report
