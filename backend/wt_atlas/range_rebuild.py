"""Recompute explicit source-range profiles from independently bound geometry.

No probability map or required-charge map is read. The CPU layer model retains
its declared straight-path limitations; this is not calibrated game ballistics.
"""
from __future__ import annotations
import copy
from collections import Counter
import json
import os
from pathlib import Path
import tempfile
import time
import zipfile
import numpy as np
from .common import read_json, write_json, file_hash, content_hash
from .cpu_model import PreparedLayers
from .forecast.data import number, safe_file
from .forecast.inventory import bind_geometry, verify_array
from .forecast.resources import usage

MODEL = 'layered-v2-cpu-first-armor-range-1'
MARKER = '.wt-range-rebuild'


def source_range_profile(profile, distance):
    if not number(distance) or distance < 0:
        raise ValueError('Invalid physical distance')
    if profile.get('mode') != 'KE' or profile.get('reference_only'):
        raise ValueError('A supported owned KE profile is required')
    if profile.get('impact_distance_m') is not None:
        raise ValueError('Use an original reference profile, not a derived range profile')
    curve = profile.get('normalized_distance_curve', [])
    reference = profile.get('distance_m')
    if (not number(reference) or not number(profile.get('penetration_mm')) or profile['penetration_mm']<=0 or
        len(curve)<2 or any(not isinstance(row,list) or len(row)!=2 or
        not all(number(x) for x in row) or row[0]<0 or not 0<row[1]<=1 for row in curve)):
        raise ValueError('Source range curve is missing or invalid')
    if curve[0] != [reference,1.] or any(b[0]<=a[0] or b[1]>a[1] for a,b in zip(curve,curve[1:])):
        raise ValueError('Reference penetration and source curve are inconsistent')
    points = [row for row in curve if row[0]==distance]
    if len(points)!=1:
        raise ValueError('Requested distance is not an exact source table point')
    provenance=profile.get('provenance',{})
    if not provenance.get('penetration_source_ammo_ids') or not provenance.get('client_version') or not provenance.get('datamine_commit'):
        raise ValueError('Exact ammunition ownership and source version are required')
    result=copy.deepcopy(profile)
    source_hash=content_hash(profile)
    result.update(name=profile['name']+'_range_'+format(distance,'g').replace('.','p')+'_'+source_hash[:8],
                  distance_m=float(distance),impact_distance_m=float(distance))
    result['provenance']['range_rebuild']={
        'source_profile_sha256':source_hash,'reference_distance_m':reference,
        'reference_penetration_mm':profile['penetration_mm'],'native_source_point':points[0],
        'impact_penetration_mm':profile['penetration_mm']*points[0][1],
        'range_reference':'first_armor_intersection','model':MODEL,
        'method':'fresh_ordered_geometry_calculation; no map rescaling'}
    return result


def rebuild(repository,catalog_path,ammo_path,config_path,output,*,targets=None,profiles=None,
            distances=(100.,500.,1000.),limit=None,memory_budget=1536*1024**2,resume=False):
    started=time.perf_counter()
    repository=Path(repository).resolve();output=Path(output)
    if any(p.is_symlink() for p in (output,*output.parents)):
        raise ValueError('Symlink output')
    output=output.resolve()
    if repository.is_relative_to(output):
        raise ValueError('Output would overwrite source repository')
    if output.exists() and any(output.iterdir()) and not (output/MARKER).is_file():
        raise ValueError('Output directory is not a managed range rebuild')
    if limit is not None and (not isinstance(limit,int) or isinstance(limit,bool) or limit<1):
        raise ValueError('Map limit must be positive')
    if memory_budget<32*1024**2:
        raise ValueError('Decoded memory budget is too small')
    catalog=read_json(catalog_path);source=read_json(ammo_path);config=read_json(config_path)
    if not isinstance(source,list) or len({p['name'] for p in source})!=len(source):
        raise ValueError('Unique source ammunition profiles required')
    original={p['name']:p for p in source}
    if profiles and set(profiles)-original.keys():raise ValueError('Unknown requested profile')
    selected=[p for p in source if not profiles or p['name'] in profiles]
    by_id={v['id']:v for v in catalog['vehicles']}
    if targets and set(targets)-by_id.keys():raise ValueError('Unknown requested target')
    selected_catalog={**catalog,'vehicles':[v for v in catalog['vehicles']
        if (not targets or v['id'] in targets) and v.get('kind')=='ground']}
    bindings,errors=bind_geometry(repository,repository/'data/work/client/jobs',selected_catalog)
    jobs={}
    for path in (repository/'data/work/client/jobs').glob('*.json'):
        job=read_json(path)
        if job.get('vehicle_id') in bindings:jobs[job['vehicle_id']]=job
    derived=[];rejections=Counter()
    for p in selected:
        for distance in sorted(set(distances)):
            try:derived.append(source_range_profile(p,distance))
            except ValueError as error:rejections[str(error)]+=1
    if not derived or not bindings:
        return {'status':'blocked','computed_maps':0,'rejections':dict(rejections),'binding_errors':errors}
    versions={(p['provenance']['client_version'],p['provenance']['datamine_commit']) for p in derived}
    if len(versions)!=1:raise ValueError('Select one physical source version')
    version,commit=next(iter(versions))
    identity={'version':version,'datamine_commit':commit,'model':MODEL,
              'cpu_code_sha256':file_hash(Path(__file__).with_name('cpu_model.py')),
              'adapter_sha256':file_hash(__file__),'numerics_sha256':content_hash(config['numerics'])}
    marker=output/MARKER
    if marker.exists() and read_json(marker)!=identity:
        raise ValueError('Output belongs to a different physical source/model; use a new version directory')
    output.mkdir(parents=True,exist_ok=True);(output/'arrays').mkdir(exist_ok=True)
    write_json(marker,identity)
    bundle_path=output/'ammunition.json'
    current_source_hashes={content_hash(p) for p in source}
    bundled={p['name']:p for p in (read_json(bundle_path) if bundle_path.exists() else [])
             if p.get('provenance',{}).get('range_rebuild',{}).get('source_profile_sha256') in current_source_hashes}
    bundled.update({p['name']:p for p in derived})
    write_json(bundle_path,sorted(bundled.values(),key=lambda p:p['name']))
    report={'schema_version':'1.0.0','model':MODEL,'identity':identity,'status':'completed',
            'planned_maps':len(bindings)*len(derived),'computed_maps':0,'reused_maps':0,
            'binding_errors':errors,'profile_rejections':dict(rejections),'errors':[],
            'source_npz_probability_maps_read':0,'raw_geometry_changed':False,'game_probability_validated':False}
    completed=0
    for target,bound in sorted(bindings.items()):
        if (bound['identity']['client_version'],bound['identity']['datamine_commit'])!=(version,commit):
            report['errors'].append({'target':target,'error':'Target/profile version mismatch'});continue
        metadata=safe_file(repository,jobs[target]['layer_metadata'].replace('\\','/'))
        with zipfile.ZipFile(metadata.with_suffix('.npz')) as archive:
            decoded=sum(item.file_size for item in archive.infolist())
        if decoded>memory_budget:
            report['errors'].append({'target':target,'error':'Decoded layer memory budget exceeded'});continue
        model=None
        for ammo in derived:
            if limit is not None and report['computed_maps']>=limit:break
            map_id=content_hash({'target':bound['target_sha256'],'ammunition':content_hash(ammo),'model':identity})
            array=output/'arrays'/(map_id+'.npz');meta_path=array.with_suffix('.json')
            common={'map_id':map_id,'target_id':target,'target_sha256':bound['target_sha256'],
                'ammo_id':ammo['name'],'ammo_profile_sha256':content_hash(ammo),'source_version':version,
                'datamine_commit':commit,'array_file':'arrays/'+array.name,'physical_distance_m':ammo['distance_m'],
                'model':identity,'range_reference':'first_armor_intersection'}
            saved=read_json(meta_path) if resume and meta_path.is_file() and array.is_file() else None
            valid=bool(saved and all(saved.get(k)==v for k,v in common.items()) and file_hash(array)==saved.get('array_sha256'))
            if valid:
                checked=verify_array((str(output),saved,memory_budget))
                valid=checked['status']=='verified'
            if valid:report['reused_maps']+=1
            else:
                if model is None:model=PreparedLayers(metadata,config)
                probability,charge,status,flags=model.calculate(ammo)
                fd,name=tempfile.mkstemp(prefix=map_id+'.',suffix='.part',dir=array.parent)
                try:
                    with os.fdopen(fd,'wb') as handle:
                        np.savez_compressed(handle,penetration_probability=probability,model_required_charge_mm=charge,
                            status=status,material_response_flags=flags,angles_deg=np.asarray(model.plan['angles_deg'],np.float64),
                            **{k:np.float64(model.plan[k]) for k in ('pixel_pitch_m','world_width_m','top_y_m','bottom_y_m')})
                    os.replace(name,array)
                finally:
                    if Path(name).exists():Path(name).unlink()
                saved={**common,'array_sha256':file_hash(array),'status_counts':{str(i):int(np.count_nonzero(status==i)) for i in range(4)}}
                checked=verify_array((str(output),saved,memory_budget))
                if checked['status']!='verified':raise ValueError('Generated array failed validation: '+str(checked))
                write_json(meta_path,saved);report['computed_maps']+=1
            completed+=1
            with (output/'events.jsonl').open('a',encoding='utf-8') as journal:
                journal.write(json.dumps({'map_id':map_id,'target':target,'profile':ammo['name'],'distance_m':ammo['distance_m'],'reused':valid})+'\n')
        if limit is not None and report['computed_maps']>=limit:break
    report.update(pending_maps=report['planned_maps']-completed,duration_seconds=time.perf_counter()-started,resources=usage(),
                  ammunition_sha256=file_hash(bundle_path),catalog_sha256=file_hash(catalog_path),source_ammunition_sha256=file_hash(ammo_path))
    if report['errors'] or report['binding_errors']:report['status']='partial'
    if report['pending_maps']:report['status']='partial'
    write_json(output/'run.json',report)
    return report

