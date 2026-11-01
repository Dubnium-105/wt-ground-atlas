"""Resumable, source-checked offline system-member research evaluation."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import numpy as np
import scipy

from .common import content_hash,file_hash,read_json,write_json
from .forecast.data import Atlas,safe_file
from .forecast.engine import ammunition_payloads
from .forecast.inventory import bind_geometry
from .forecast.system_batch_evaluation import AimIntervalCache,evaluate_cached
from .forecast.system_map_cache import SystemMapCache
from .forecast.system_physics import declared_members
from .system_batch_planning import validate_plan


def stamp(path):
    s=path.stat();return (s.st_size,s.st_mtime_ns,s.st_ctime_ns)


class SourceGuard:
    """Rehash a pinned source whenever its filesystem identity changes."""
    def __init__(self):self.rows={}
    def add(self,path,expected=None,*,normalize=False):
        path=Path(path).resolve();before=stamp(path)
        value=hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest() if normalize else file_hash(path)
        if stamp(path)!=before or expected is not None and value!=expected:raise ValueError('Pinned source changed: '+str(path))
        old=self.rows.get(path)
        if old and (old[0],old[2])!=(value,normalize):raise ValueError('Conflicting pinned source: '+str(path))
        self.rows[path]=(value,before,normalize);return value
    def check(self):
        for path,(expected,previous,normalize) in list(self.rows.items()):
            current=stamp(path)
            if current!=previous:self.add(path,expected,normalize=normalize)


def report_key(identity,request):return content_hash({'batch_identity':identity,'request':request})


def run(plan_path,assembly_path,atlas_path,catalog_path,ammunition_path,config_path,bindings_path,output,
        *,memory_budget=512*1024**2,aim_entries=50000,resume=False,limit=None):
    root=Path(__file__).resolve().parents[2];atlas_path=Path(atlas_path).resolve();output=Path(output)
    if any(p.is_symlink() for p in (output,*output.parents)):raise ValueError('Symlink batch output')
    output=output.resolve();sources=[Path(p) for p in (catalog_path,ammunition_path,config_path,bindings_path)]
    inputs=[Path(plan_path),Path(assembly_path),*sources,atlas_path/'.wt-system-research-atlas.json']
    if (memory_budget<64*1024**2 or limit is not None and (type(limit) is not int or limit<1) or
        output.is_relative_to(atlas_path) or atlas_path.is_relative_to(output) or any(p.resolve().is_relative_to(output) for p in inputs)):
        raise ValueError('Invalid batch budget, limit or output location')
    guard=SourceGuard();plan_sha=guard.add(plan_path);assembly_sha=guard.add(assembly_path)
    hashes={str(p):guard.add(p) for p in sources};manifest_sha=guard.add(atlas_path/'.wt-system-research-atlas.json')
    plan=read_json(plan_path);assembly=read_json(assembly_path);manifest=read_json(atlas_path/'.wt-system-research-atlas.json')
    if ({str(p.resolve()):hashes[str(p)] for p in sources}!=plan.get('input_sha256') or
        assembly.get('plan_sha256')!=plan_sha or assembly.get('atlas_manifest_sha256')!=manifest_sha or
        assembly.get('publishable') is not False or manifest.get('publishable') is not False):
        raise ValueError('Research batch input identity mismatch')
    for name,sha in plan.get('code_sha256_lf',{}).items():guard.add(safe_file(root,name),sha,normalize=True)
    if not plan.get('code_sha256_lf'):raise ValueError('Research plan code identity missing')
    names=('backend/wt_atlas/system_batch_runner.py','backend/wt_atlas/system_batch_planning.py',
        'backend/wt_atlas/system_research_atlas.py','backend/wt_atlas/forecast/system_batch_evaluation.py',
        'backend/wt_atlas/forecast/system_map_cache.py','backend/wt_atlas/forecast/system_physics.py',
        'backend/wt_atlas/forecast/aim.py','backend/wt_atlas/forecast/engine.py','backend/wt_atlas/forecast/data.py',
        'backend/wt_atlas/forecast/inventory.py','backend/wt_atlas/forecast/belts.py','backend/wt_atlas/forecast/delivery.py','backend/wt_atlas/forecast/profile_history.py',
        'backend/wt_atlas/npz_io.py','backend/wt_atlas/common.py','scripts/evaluate_system_research_batch.py')
    code_hashes={name:guard.add(safe_file(root,name),normalize=True) for name in names}
    catalog=read_json(catalog_path);config=read_json(config_path);receipt=read_json(bindings_path)
    physical=Atlas(atlas_path,ammunition_path,catalog,memory_budget=memory_budget//2)
    atlas=SystemMapCache(physical,memory_budget//2);keys=validate_plan(plan,catalog,atlas,config)
    records=manifest.get('records',[]);record_keys={(r['target_id'],r['profile_id'],r['distance_m']) for r in records}
    if len(records)!=len(keys) or record_keys!=keys or assembly.get('maps')!=len(keys) or assembly.get('candidates')!=len(plan['requests']):
        raise ValueError('Research atlas does not match its plan')
    for row in records:
        key=(row['target_id'],row['profile_id'],row['distance_m']);map_id=atlas.map_for(*key)
        if (map_id!=row['map_id'] or atlas.meta[map_id]['metadata_sha256']!=row['metadata_sha256'] or
                atlas.meta[map_id]['array_sha256']!=row['array_sha256']):raise ValueError('Research atlas map identity mismatch')
    targets={key[0] for key in keys};selected={**catalog,'vehicles':[v for v in catalog['vehicles'] if v['id'] in targets]}
    repository=Path(receipt['repository']);jobs=Path(receipt['jobs'])
    def geometry():
        bound,errors=bind_geometry(repository,jobs,selected)
        if errors or set(bound)!=targets or any(receipt.get('bindings',{}).get(k,{}).get('target_sha256')!=v['target_sha256'] for k,v in bound.items()):
            raise ValueError('System geometry source verification failed')
        return bound
    bound=geometry();job_ids=set()
    for path in sorted(jobs.glob('*.json')):
        job=read_json(path);identifier=job.get('vehicle_id')
        if identifier not in targets or job.get('status')!='complete':continue
        if identifier in job_ids:raise ValueError('Ambiguous system geometry jobs')
        job_ids.add(identifier);guard.add(path,bound[identifier]['source_job_sha256'])
        parts=[safe_file(repository,job[k].replace('\\','/')) for k in ('profile_path','layer_metadata','raw_path')]
        for source in (*parts,parts[1].with_suffix('.npz')):guard.add(source)
    if job_ids!=targets or geometry()!=bound:raise ValueError('System geometry changed during batch setup')
    jobs_stamp=stamp(jobs)
    runtime={'python':sys.version,'numpy':np.__version__,'scipy':scipy.__version__}
    identity={'schema_version':'1.0.0','plan_sha256':plan_sha,'assembly_sha256':assembly_sha,
              'atlas_manifest_sha256':manifest_sha,'input_sha256':hashes,'source_code_sha256_lf':code_hashes,
              'runtime':runtime,'skill_profile':'normal','publishable':False}
    marker=output/'.wt-system-batch.json'
    if output.exists():
        if not resume or marker.is_symlink() or not marker.is_file() or read_json(marker)!=identity:
            raise ValueError('Existing batch output does not match these inputs')
    else:output.mkdir(parents=True);write_json(marker,identity)
    aim=AimIntervalCache(config['aim'],max_entries=aim_entries);vehicles=atlas.catalog
    def sort_key(request):
        vehicle=vehicles[request['attacker_id']];ammo=next(a for a in vehicle['ammo'] if a['id']==request['ammo_id'])
        weapon=ammo.get('weapon_profile') or vehicle.get('firepower',{});gun=weapon.get('traverse_deg_s')
        stabilized=vehicle.get('sensors',{}).get('stabilizer');stabilized=bool(stabilized) if stabilized is not None else None
        return (request['system_id'],tuple(sorted(request['profiles'].items())),str(gun),str(stabilized),request['attacker_id'],request['ammo_id'])
    requests=sorted(plan['requests'],key=sort_key);counts=Counter();processed=0
    def state(status,last=None):
        write_json(output/'progress.json',{'status':status,'pid':os.getpid(),'total':len(requests),'processed':processed,
            'computed':counts['computed'],'reused':counts['reused'],'last':last,'map_loads':physical.loads,
            'map_cache_hits':atlas.hits,'map_cache_bytes':atlas.cached_bytes,'aim_cache_hits':aim.hits,
            'aim_calculations':aim.calculations,'publishable':False,'formal_rankings':0})
    try:
        state('running')
        for request in requests:
            if limit is not None and processed>=limit:break
            guard.check()
            if stamp(jobs)!=jobs_stamp:
                if geometry()!=bound:raise ValueError('System geometry jobs changed')
                jobs_stamp=stamp(jobs)
            key=report_key(identity,request);path=output/(key+'.json');checksum=output/(key+'.sha256')
            if path.is_symlink() or checksum.is_symlink():raise ValueError('Symlink batch result')
            reused=False
            if resume and path.is_file() and checksum.is_file():
                expected=read_json(checksum)
                if len(expected)!=64 or file_hash(path)!=expected:raise ValueError('Existing batch result checksum mismatch')
                result=read_json(path)
                if (result.get('batch_job_key')!=key or result.get('system_id')!=request['system_id'] or
                    result.get('attacker_id')!=request['attacker_id'] or result.get('ammo_id')!=request['ammo_id'] or
                    result.get('input_sha256')!=hashes or result.get('source_code_sha256_lf')!=code_hashes or
                    result.get('runtime')!=runtime or
                    result.get('publishable') is not False or result.get('formal_rankings')!=0):
                    raise ValueError('Existing batch result identity mismatch')
                for map_id in {m['map_id'] for row in result['rows'] for m in row['members'] if m.get('map_id')}:
                    atlas.validate(map_id)
                reused=True
            if not reused:
                attacker=vehicles[request['attacker_id']];payloads=[x for x in ammunition_payloads(atlas.bindings(attacker)) if x[0].get('id')==request['ammo_id']]
                if len(payloads)!=1:raise ValueError('Batch ammunition selection changed')
                ammo,profiles,_=payloads[0];members=declared_members(catalog,request['system_id'])
                rows=[evaluate_cached(atlas,members,bound,attacker,ammo,profiles,s,config,'normal',aim) for s in config['scenarios']]
                if any(m['status']!='source_bound_map' for row in rows for m in row['members']):raise ValueError('Planned member map missing')
                for map_id in {m['map_id'] for row in rows for m in row['members']}:atlas.validate(map_id)
                guard.check()
                if stamp(jobs)!=jobs_stamp:
                    if geometry()!=bound:raise ValueError('System geometry jobs changed')
                    jobs_stamp=stamp(jobs)
                result={'schema_version':'1.0.0','batch_job_key':key,'system_id':request['system_id'],
                    'attacker_id':request['attacker_id'],'ammo_id':request['ammo_id'],'skill_profile':'normal','rows':rows,
                    'input_sha256':hashes,'source_code_sha256_lf':code_hashes,'publishable':False,'formal_rankings':0,
                    'runtime':runtime,
                    'target_selection_weights':'unknown','scope':'Conditional penetration of a selected source member under declared aim assumptions. No system-level destruction or encounter distribution is inferred.'}
                write_json(path,result);write_json(checksum,file_hash(path));counts['computed']+=1
            else:counts['reused']+=1
            processed+=1;state('running',last={'system_id':request['system_id'],'attacker_id':request['attacker_id'],'ammo_id':request['ammo_id']})
        state('complete' if processed==len(requests) else 'limit_reached')
        return {'processed':processed,'total':len(requests),'computed':counts['computed'],'reused':counts['reused'],
                'map_loads':physical.loads,'map_cache_hits':atlas.hits,'aim_cache_hits':aim.hits,'aim_calculations':aim.calculations}
    except BaseException as error:
        state('failed',last={'error':str(error)});raise
