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


def ce_source_evidence(profile, source_root, *, historical_adapter_sha256=None):
    """Verify the exact projectile's constant jet penetration in captured BLK.

    A scalar cumulativeDamage.armorPower supports conditional penetration at
    impact, not a claim about flight range, arrival probability or damage.
    """
    from .native_binding.ordered_blk import parse_ordered_blk
    provenance = profile.get('provenance', {})
    if provenance.get('nested_missile_adapter'):
        if historical_adapter_sha256:raise ValueError('Nested missile evidence cannot use a historical chemical adapter')
        from .nested_missile_jet import source_evidence
        return source_evidence(profile, source_root)
    if provenance.get('chemical_energy'):
        from .chemical_projectiles import source_evidence
        current=source_evidence(profile, source_root)
        if not historical_adapter_sha256 or historical_adapter_sha256==current['adapter_sha256']:
            return current
        # These exact frozen source bytes are retained to replay previously
        # emitted receipts; current validation must also pass before reuse.
        from importlib import import_module
        historical={
            'f89b2db073324f2ed748149f994282768ea37ecd2afa11d3f8cd1aa2ad8ccc0c':'chemical_projectiles_v1',
            '26f2d0d0d3a551942c2ce2d448d3b13bd9547748058031586a6f3f2a3d836b6e':'chemical_projectiles_v2',
        }
        name=historical.get(historical_adapter_sha256)
        if not name:raise ValueError('Unknown historical chemical adapter')
        path=Path(__file__).with_name(name+'.py')
        if file_hash(path)!=historical_adapter_sha256:raise ValueError('Historical chemical adapter checksum mismatch')
        previous=import_module('.'+name,__package__).source_evidence(profile,source_root)
        if ({k:v for k,v in previous.items() if k!='adapter_sha256'} !=
            {k:v for k,v in current.items() if k!='adapter_sha256'}):
            raise ValueError('Historical chemical evidence differs from current validation')
        return previous
    path = provenance.get('source_weapon_path')
    if source_root is None or not path or not provenance.get('source_weapon_sha256'):
        raise ValueError('CE range requires the captured source weapon file')
    source = safe_file(Path(source_root), path)
    if file_hash(source) != provenance['source_weapon_sha256']:
        raise ValueError('CE source weapon hash mismatch')
    bullet = provenance.get('bullet_name')
    bullet_type = provenance.get('bullet_type')
    if not bullet or bullet_type not in ('heat_tank', 'heat_fs_tank', 'heat_grenade_tank', 'heat_mp_vt_tank'):
        raise ValueError('Unsupported CE source projectile')
    matches = []
    def visit(block, location):
        params = {p['name']: p['value'] for p in block['params']}
        if params.get('bulletName') == bullet:
            powers = [p['value'] for child in block['children'] if child['name'] == 'cumulativeDamage'
                      for p in child['params'] if p['name'] == 'armorPower' and p['type'] in ('r', 'i')]
            if params.get('bulletType') != bullet_type or len(powers) != 1 or powers[0] != profile['penetration_mm']:
                raise ValueError('CE projectile does not match source constant penetration')
            matches.append(location)
        for i, child in enumerate(block['children']):
            visit(child, location + '/' + child['name'] + '[' + str(i) + ']')
    visit(parse_ordered_blk(source), '@root')
    if not matches:
        raise ValueError('Exact CE projectile absent from source')
    return {'source_weapon_path': path, 'source_weapon_sha256': file_hash(source),
            'bullet_name': bullet, 'bullet_type': bullet_type, 'block_paths': matches,
            'parameter': 'cumulativeDamage.armorPower', 'penetration_mm': profile['penetration_mm']}


def source_range_profile(profile, distance, source_root=None, *, expected_evidence=None):
    if not number(distance) or distance < 0:
        raise ValueError('Invalid physical distance')
    if profile.get('mode') not in ('KE', 'CE') or profile.get('reference_only'):
        raise ValueError('A supported owned KE or CE profile is required')
    if profile.get('impact_distance_m') is not None or profile.get('provenance', {}).get('range_rebuild'):
        raise ValueError('Use an original reference profile, not a derived range profile')
    provenance=profile.get('provenance',{})
    if not provenance.get('penetration_source_ammo_ids') or not provenance.get('client_version') or not provenance.get('datamine_commit'):
        raise ValueError('Exact ammunition ownership and source version are required')
    if profile['mode'] == 'CE':
        if (not number(profile.get('penetration_mm')) or profile['penetration_mm'] <= 0 or
                profile.get('normalized_distance_curve')):
            raise ValueError('CE constant source profile is invalid')
        if expected_evidence is not None and not isinstance(expected_evidence,dict):
            raise ValueError('Invalid expected source evidence')
        expected_adapter=(expected_evidence or {}).get('adapter_sha256')
        evidence = ce_source_evidence(profile, source_root,
            historical_adapter_sha256=expected_adapter if not provenance.get('nested_missile_adapter') else None)
        if expected_evidence is not None and evidence!=expected_evidence:
            raise ValueError('Recorded chemical source evidence mismatch')
        result = copy.deepcopy(profile)
        source_hash = content_hash(profile)
        result.update(name=profile['name']+'_range_'+format(distance,'g').replace('.','p')+'_'+source_hash[:8],
                      distance_m=float(distance))
        result['provenance']['range_rebuild'] = {
            'source_profile_sha256': source_hash, 'source_evidence': evidence,
            'impact_penetration_mm': profile['penetration_mm'],
            'range_reference': 'conditional_at_impact', 'model': 'layered-v2-ce-source-constant-1',
            'method': 'fresh_ordered_geometry_calculation; source scalar CE penetration; no flight or hit-rate claim'}
        return result
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

