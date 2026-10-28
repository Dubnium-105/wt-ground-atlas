"""Export deterministic sampled GRP paths for vehicle-owned ammunition scoring.

This is a straight-path, pristine-geometry approximation, separate from the
reference-shell heatmaps. Ammunition is evaluated at runtime using its own
source penetration table. No reference shell probabilities are substituted.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.cpu_model import prepare_materials
from wt_atlas.common import read_json, write_json, file_hash


def sample_target(job_path, config, sample_count):
    job=read_json(job_path)
    meta_path=ROOT/job['layer_metadata']
    meta=read_json(meta_path)
    if not meta.get('all_inward_node_intervals_retained'):
        raise ValueError('Incomplete intervals: '+job['vehicle_id'])
    packet=meta_path.with_suffix('.npz')
    if file_hash(packet)!=meta['arrays_sha256']:
        raise ValueError('Layer hash mismatch: '+job['vehicle_id'])
    with np.load(packet,allow_pickle=False) as source:
        data={key:source[key] for key in ['ray_id','node','entry_m','exit_m','incidence_cos','extra_entry_m']}
    order=np.lexsort((data['entry_m'],data['ray_id']))
    data={k:v[order] for k,v in data.items()}
    kinds,flags,ke,_=prepare_materials(meta['materials'],{'mechanism_id':0},config['numerics'])
    _,_,ce,_=prepare_materials(meta['materials'],{'mechanism_id':10},config['numerics'])
    rays=data['ray_id'];nodes=data['node'];plan=meta['plan'];pixels=plan['width']*plan['height']
    protected=np.unique(rays[kinds[nodes]==2])
    views=[]
    for ai,angle in enumerate(plan['angles_deg']):
        available=protected[(protected>=ai*pixels)&(protected<(ai+1)*pixels)]
        if not len(available):
            views.append({'angle_deg':angle,'protected_rays':0,'rays':[]});continue
        # Equal-area systematic strata over every protected-target pixel.
        picks=available[np.floor((np.arange(min(sample_count,len(available)))+.5)*len(available)/min(sample_count,len(available))).astype(int)]
        samples=[]
        for ray in picks:
            lo=np.searchsorted(rays,ray,'left');hi=np.searchsorted(rays,ray,'right')
            slots=np.arange(lo,hi);ns=nodes[slots]
            target=np.min(data['entry_m'][slots[kinds[ns]==2]])
            slots=slots[data['entry_m'][slots]<target]
            layers=[];unknown=False
            for i in slots:
                node=nodes[i];kind=kinds[node]
                if kind==0:unknown=True
                if kind!=1:continue
                solid=bool(flags[node]&1);reactive=bool(flags[node]&4)
                if data['extra_entry_m'][i]<target or (solid and not np.isfinite(data['exit_m'][i])):
                    unknown=True;continue
                cosine=float(max(config['numerics']['min_cosine'],data['incidence_cos'][i]))
                responses=[]
                for material in [ke[node],ce[node]]:
                    thickness,quality,minimum,_,normal_cap,effective_cap,through,_=map(float,material)
                    normal=min(thickness*quality,normal_cap)
                    los=normal
                    if solid:
                        los=max(0,quality*float(data['exit_m'][i]-data['entry_m'][i])*1000)
                        normal=min(max(cosine*los,minimum),normal_cap)
                    elif not reactive:
                        los=min(min(1/cosine,config['numerics']['max_geometry_scale'])*thickness*quality,effective_cap)
                    # Keep normal thickness, LOS and caps separately: projectile
                    # angle tables replace geometric slope, never multiply it twice.
                    responses.extend([normal,los,min(effective_cap,1e7),through])
                layers.append([round(v,4) for v in responses]+[round(cosine,6),1 if solid else 2 if reactive else 0])
            samples.append(None if unknown else layers)
        views.append({'angle_deg':angle,'protected_rays':int(len(available)),'rays':samples})
    return {'target_id':job['vehicle_id'],'source_version':config['scope']['source_version'],
            'geometry_sha256':job['grp_sha256'],'configuration_sha256':job['profile_sha256'],
            'layers_sha256':meta['arrays_sha256'],'views':views}


def build(workers=6,samples=64,limit=None):
    started=time.perf_counter();config=read_json(ROOT/'config/pipeline.json')
    jobs=sorted((ROOT/'data/work/client/jobs').glob('*.json'))
    if limit:jobs=jobs[:limit]
    targets=[];cache=ROOT/'data/work/client/decision-layers';cache.mkdir(exist_ok=True)
    code_hash=file_hash(__file__)
    pending=[]
    for path in jobs:
        job=read_json(path);meta=read_json(ROOT/job['layer_metadata'])
        key=hashlib.sha256((code_hash+meta['arrays_sha256']+job['profile_sha256']+str(samples)).encode()).hexdigest()
        out=cache/(key+'.json')
        if out.exists():targets.append(read_json(out))
        else:pending.append((path,out))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(sample_target,path,config,samples):out for path,out in pending}
        for future in as_completed(futures):
            target=future.result();write_json(futures[future],target);targets.append(target)
            if len(targets)%100==0:print(json.dumps({'targets':len(targets),'total':len(jobs),'seconds':round(time.perf_counter()-started,1)}),flush=True)
    targets.sort(key=lambda x:x['target_id'])
    bundle={'schema_version':1,'model':'sampled-grp-owned-ammunition-v1','source_version':config['scope']['source_version'],
            'samples_per_view':samples,'layer_layout':['ke_normal','ke_los','ke_cap','ke_through','ce_normal','ce_los','ce_cap','ce_through','incidence_cos','path_kind_0_plate_1_solid_2_reactive'],
            'mechanisms':{'kinetic':{'index':0,'name':'generic'},'chemical':{'index':10,'name':'cumulative'}},
            'scope':'Uniform protected-target projection sampling; own-ammunition source penetration and slope tables; straight-path additive layer approximation; no ricochet, post-penetration damage, ERA activation or tandem-stage simulation.',
            'builder_sha256':code_hash,'targets':targets}
    output=ROOT/'data/snapshots/target-layers.json';write_json(output,bundle)
    report={'targets':len(targets),'views':sum(len(t['views']) for t in targets),'sampled_rays':sum(len(v['rays']) for t in targets for v in t['views']),
            'unknown_rays':sum(sum(r is None for r in v['rays']) for t in targets for v in t['views']),
            'sha256':file_hash(output),'bytes':output.stat().st_size,'duration_s':round(time.perf_counter()-started,2),'model':bundle['model'],'scope':bundle['scope']}
    write_json(ROOT/'reports/target-layers.json',report);print(json.dumps(report),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--workers',type=int,default=6);parser.add_argument('--samples',type=int,default=64);parser.add_argument('--limit',type=int)
    args=parser.parse_args();build(args.workers,args.samples,args.limit)
