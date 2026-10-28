#!/usr/bin/env python3
"""Assemble deterministic static assets; source/catalog, model and geometry stay separate."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import shutil
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')

def write_json(root,name,value):
    content=encode(value)
    digest=hashlib.sha256(content).hexdigest()
    logical=Path(name)
    relative=logical.with_name(f'{logical.stem}.{digest[:16]}{logical.suffix}')
    path=root/relative
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(content)
    compressed=gzip.compress(content,mtime=0)
    path.with_name(path.name+'.gz').write_bytes(compressed)
    return {'url':relative.as_posix(),'gzip_url':relative.as_posix()+'.gz','sha256':digest,'bytes':len(content),'gzip_bytes':len(compressed)}

def build(root:Path=ROOT):
    started=time.perf_counter()
    web=root/'web';data=web/'data';data.mkdir(parents=True,exist_ok=True)
    catalog=load(root/'data/snapshots/catalog.json')
    changes_path=root/'data/snapshots/changes.json'
    if not changes_path.exists():
        raise FileNotFoundError('A sourced changes.json (or explicit unavailable status) is required; scenarios are never fabricated.')
    changes=load(changes_path);model=load(root/'config/model.json');ui=load(root/'config/ui.json')
    index_fields=('id','name','nation','kind','role','rank','branch','folder','folder_members','predecessors','special','br','br_source_mode','image_url','wiki_url','configuration_id','equivalence_group','equivalence_verified')
    index_vehicles=[{key:vehicle.get(key) for key in index_fields} for vehicle in catalog['vehicles']]
    light_fields=(*index_fields,'mobility','firepower','protection','sensors','air_defence','auxiliary','ammo','loadouts','hardpoints','payload_limits','max_payload_kg','system_id','system_role','multi_vehicle_system','configuration_version','geometry_version','source_version')
    engine_vehicles=[]
    for vehicle in catalog['vehicles']:
        item={key:vehicle.get(key) for key in light_fields if key in vehicle}
        # Scenario.old may address entire compound fields; preserve exact structure.
        item['client_collision']=vehicle.get('source_attributes',{}).get('client')
        item['ammo']=vehicle.get('ammo',[])
        item['loadouts']=vehicle.get('loadouts',[])
        engine_vehicles.append(item)
    manifest={
        'schema_version':'1.0.0','snapshot_id':catalog['snapshot']['id'],
        'snapshot':catalog['snapshot'], 'model_version':model['model_version'],
        'public_runtime':'static_HTTP_and_browser_Web_Worker',
        'raw_geometry_required_on_first_load':False,
        'ui':write_json(web,'data/config/ui.json',ui),
        'model':write_json(web,'data/config/model.json',model),
        'catalog_index':write_json(web,'data/catalog/index.json',{'schema_version':catalog['schema_version'],'snapshot':catalog['snapshot'],'nations':catalog['nations'],'vehicles':index_vehicles}),
        'engine_catalog':write_json(web,'data/catalog/engine.json',{'schema_version':catalog['schema_version'],'snapshot':catalog['snapshot'],'nations':catalog['nations'],'vehicles':engine_vehicles}),
        'scenario':write_json(web,'data/scenarios/planned.json',changes),
        'nations':{},'vehicle_details':{},'recommendations':{},'national_recommendations':[],
        'sources':{'datamine':'https://github.com/gszabi99/War-Thunder-Datamine','statistics':'https://statshark.net/globalstatcharts'}
    }
    for nation in catalog['nations']:
        vehicles=[v for v in catalog['vehicles'] if v['nation']==nation['id']]
        manifest['nations'][nation['id']]=write_json(web,f'data/nations/{nation["id"]}.json',{'snapshot':catalog['snapshot'],'nation':nation,'vehicles':vehicles})
    # Detail locations derive from the catalog; they remain out of the startup payload.
    for vehicle in catalog['vehicles']:
        manifest['vehicle_details'][vehicle['id']]=write_json(web,f'data/vehicles/{vehicle["id"]}.json',vehicle)
    rec_index_path=data/'recommendations/index.json'
    if rec_index_path.exists():
        rec_index=load(rec_index_path)
        expected_catalog=hashlib.sha256((root/'data/snapshots/catalog.json').read_bytes()).hexdigest()
        expected_config=hashlib.sha256((root/'config/model.json').read_bytes()).hexdigest()
        expected_scenario=hashlib.sha256(changes_path.read_bytes()).hexdigest()
        expected_engine=hashlib.sha256((root/'shared/engine.mjs').read_bytes()).hexdigest()
        probability_summary_path=root/'data/snapshots/decision-probabilities.json'
        summary_bytes=probability_summary_path.read_bytes() if probability_summary_path.exists() else b'{"schema_version":"1.0.0","records":[],"status":"no_summary_bundle"}'
        expected_target_layers=hashlib.sha256((root/'data/snapshots/target-layers.json').read_bytes()).hexdigest()
        expected_probability_summaries=hashlib.sha256(summary_bytes).hexdigest()
        if any(rec_index.get(key)!=value for key,value in [('target_layers_sha256',expected_target_layers),('catalog_sha256',expected_catalog),('config_sha256',expected_config),('scenario_sha256',expected_scenario),('engine_sha256',expected_engine),('probability_summaries_sha256',expected_probability_summaries)]):
            raise ValueError('Recommendation cache does not match catalog/scenario/model/engine/physics summaries. Run scripts/build_decisions.mjs first.')
        expected_shards={(nation['id'],version) for nation in catalog['nations'] for version in ('current','planned')}
        if {(item['nation'],item['version']) for item in rec_index['files']}!=expected_shards:
            raise ValueError('Static release requires both versions of every nation; this is only a partial decision build.')
        for file in rec_index['files']:
            url='data/recommendations/'+file['url']
            manifest['recommendations'][f'{file["version"]}/{file["nation"]}']={key:file[key] for key in ('sha256','bytes','gzip_bytes')}
            manifest['recommendations'][f'{file["version"]}/{file["nation"]}'].update(url=url,gzip_url=url+'.gz')
            rec=load(web/url)
            best=rec.get('best')
            manifest['national_recommendations'].append({'nation':file['nation'],'version':file['version'],'br_caps':len(file['caps']),'best':{key:best.get(key) for key in ('actual_br','br','candidate_cap','lineup_ids','score','lower','upper','coverage','metrics')} if best else None})
        manifest['impact']=write_json(web,'data/impact/index.json',{'impact':rec_index['impact'],'audit':rec_index['audit'],'build_key':rec_index['build_key']})
        decision_report=load(data/'recommendations/build-report.json')
        stable_decision_report={key:value for key,value in decision_report.items() if key not in ('elapsed_ms','cache_invalidated_by','prior_build_key')}
        manifest['decision_build_report']=write_json(web,'data/reports/decision-build.json',stable_decision_report)
    else:
        raise FileNotFoundError('No nationwide precomputed decisions; run scripts/build_decisions.mjs first.')
    probability_path=data/'probability/index.json'
    if probability_path.exists():
        probability=load(probability_path);raw=probability_path.read_bytes()
        probability_path.with_name('index.json.gz').write_bytes(gzip.compress(raw,mtime=0))
        manifest['probability']={'url':'data/probability/index.json','gzip_url':'data/probability/index.json.gz','sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'gzip_bytes':len(gzip.compress(raw,mtime=0))}
        manifest['probability_summary']={'targets':len({e['target_id'] for e in probability['entries']}),'profiles':len(probability['entries']),'views':sum(len(e['shards']) for e in probability['entries']),'source_version':probability.get('source_version')}
    audit_path=root/'reports/source-audit.json'
    manifest['audit']=write_json(web,'data/reports/source-audit.json',load(audit_path) if audit_path.exists() else {'status':'missing_source_audit','issues':['No source audit was provided.']})
    probability_reports={}
    if catalog['snapshot'].get('client_source'):
        manifest['client_summary']=catalog['snapshot']['client_source']
    if (data/'probability/export-report.json').exists():
        probability_reports['full_atlas_quantization']=load(data/'probability/export-report.json')
    for file in (root/'reports').glob('*.json'):
        if any(word in file.stem for word in ('probability','cpu-model','cpu-geometry','pipeline','export')):
            probability_reports[file.stem]=load(file)
        elif file.stem in ('client-binding-validation','client-raster-validation','client-sources','client-compute'):
            probability_reports[file.stem]=load(file)
    manifest['probability_report']=write_json(web,'data/reports/probability.json',probability_reports)
    decision_probability_path=root/'data/snapshots/decision-probabilities.json'
    if decision_probability_path.exists():
        manifest['decision_probabilities']=write_json(web,'data/physics/decision-probabilities.json',load(decision_probability_path))
    layers=load(root/'data/snapshots/target-layers.json')
    manifest['target_layers']={}
    # Load only the requested target orientation in the browser worker.
    for angle in sorted({view['angle_deg'] for target in layers['targets'] for view in target['views']}):
        shard={**layers,'targets':[{**target,'views':[view for view in target['views'] if view['angle_deg']==angle]} for target in layers['targets']]}
        manifest['target_layers'][str(angle)]=write_json(web,f'data/physics/target-layers-{angle}.json',shard)
    manifest['target_layer_summary']=load(root/'reports/target-layers.json')
    (web/'shared').mkdir(exist_ok=True)
    shutil.copy2(root/'shared/engine.mjs',web/'shared/engine.mjs')
    core_files=[p for p in web.rglob('*') if p.is_file() and 'data' not in p.relative_to(web).parts and p.suffix in ('.html','.css','.js','.mjs','.svg')]
    core_gzip_bytes=sum(len(gzip.compress(path.read_bytes(),mtime=0)) for path in core_files)
    bundled_fonts_bytes=sum(path.stat().st_size for path in (web/'assets').glob('*.woff2'))
    default_nation=ui['default_nation'] if ui['default_nation'] in manifest['nations'] else catalog['nations'][0]['id']
    initial_data_bytes=sum(manifest[name]['gzip_bytes'] for name in ('ui','model','catalog_index','scenario'))+manifest['nations'][default_nation]['gzip_bytes']+manifest['recommendations'][f'current/{default_nation}']['gzip_bytes']
    base_report={'snapshot_id':catalog['snapshot']['id'],'catalog_count':len(catalog['vehicles']),'nation_count':len(catalog['nations']),'precomputed_shards':len(manifest['recommendations']),'core_gzip_bytes':core_gzip_bytes,'default_view_data_gzip_bytes_excluding_manifest':initial_data_bytes,'raw_geometry_first_load_files':0,'public_database_required':False,'public_GPU_required':False,'business_API_required':False,'worker_engine_catalog_gzip_bytes':manifest['engine_catalog']['gzip_bytes'],'default_nation':default_nation,'content_addressed':True,'numerical_game_inputs_embedded_in_UI':False}
    base_report['bundled_fonts_bytes']=bundled_fonts_bytes
    manifest['resource_summary']={'核心资源 gzip 字节':core_gzip_bytes,'本地字体字节':bundled_fonts_bytes,'默认视图数据 gzip 字节（不含清单）':initial_data_bytes,'仅按需计算目录 gzip 字节':manifest['engine_catalog']['gzip_bytes'],'预计算国家 / 版本分片':len(manifest['recommendations']),'首屏原始 GRP / NPZ 文件':0}
    manifest['build_report']=write_json(web,'data/reports/web-build.json',base_report)
    manifest_raw=encode(manifest)
    (data/'manifest.json').write_bytes(manifest_raw)
    (data/'manifest.json.gz').write_bytes(gzip.compress(manifest_raw,mtime=0))
    # Remove only generated, unreferenced hash files from our own build directories.
    referenced=set()
    def collect_urls(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in ('url','gzip_url') and isinstance(item,str) and item.startswith('data/'):
                    referenced.add(item)
                else:collect_urls(item)
        elif isinstance(value,list):
            for item in value:collect_urls(item)
    collect_urls(manifest)
    for directory in ('config','catalog','nations','vehicles','scenarios','impact','reports','physics'):
        for path in (data/directory).glob('*'):
            if path.is_file() and path.relative_to(web).as_posix() not in referenced:path.unlink()
    rec_keep={'index.json','build-report.json'}
    for item in rec_index['files']:
        rec_keep.update((item['url'],item['url']+'.gz',item['alias']))
    for path in (data/'recommendations').iterdir():
        if path.is_file() and path.name not in rec_keep:path.unlink()
    base_report['manifest_raw_bytes']=len(manifest_raw)
    base_report['manifest_gzip_bytes']=len(gzip.compress(manifest_raw,mtime=0))
    base_report['default_first_load_gzip_bytes']=initial_data_bytes+base_report['manifest_gzip_bytes']+core_gzip_bytes+bundled_fonts_bytes
    base_report['first_load_measurement_note']='Core/data use gzip sizes; bundled WOFF2 uses its encoded size. Browser HTTP transfer measurements are recorded separately.'
    def published_file(path):
        relative=path.relative_to(web)
        if len(relative.parts)>2 and relative.parts[0]=='data':
            if relative.parts[1] in ('config','catalog','nations','vehicles','scenarios','impact','reports','physics'):
                return relative.as_posix() in referenced
            if relative.parts[1]=='recommendations':return path.name in rec_keep
        return True
    public_files=[p for p in web.rglob('*') if p.is_file() and published_file(p)]
    base_report['static_total_bytes']=sum(p.stat().st_size for p in public_files)
    base_report['static_published_file_count']=len(public_files)
    base_report['duration_s']=time.perf_counter()-started
    (root/'reports/web-build.json').write_text(json.dumps(base_report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(base_report,ensure_ascii=False))
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args()
    build(args.root.resolve())
