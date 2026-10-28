#!/usr/bin/env python3
"""Assemble deterministic static assets; source/catalog, model and geometry stay separate."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import shutil
import tempfile
import time
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
# Also support importlib-based release and test callers.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_lib import (MARKER, copy_sources, copy_probability, deployment_settings,
                      file_hash, local_file, probability_base_url as normalize_base_url,
                      probability_directory, site_directory, validate_site)
from decision_bundle import load_runtime_bundle

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

def _assemble(root: Path, web: Path, mode: str, base_url: str, settings: dict):
    started=time.perf_counter()
    copy_sources(root, web)
    data=web/'data';data.mkdir(parents=True,exist_ok=True)
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
        'deployment':{'probability_mode':mode,'probability_external':mode=='external'},
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
    rec_source=root/'data/derived/recommendations'
    rec_index_path=rec_source/'index.json'
    if rec_index_path.exists():
        rec_index=load(rec_index_path)
        expected_catalog=file_hash(root/'data/snapshots/catalog.json')
        expected_config=file_hash(root/'config/model.json')
        expected_scenario=file_hash(changes_path)
        expected_engine=file_hash(root/'shared/engine.mjs')
        probability_summary_path=root/'data/snapshots/decision-probabilities.json'
        expected_target_layers=file_hash(root/'data/snapshots/target-layers.json')
        expected_probability_summaries=(file_hash(probability_summary_path) if probability_summary_path.exists()
            else hashlib.sha256(b'{"schema_version":"1.0.0","records":[],"status":"no_summary_bundle"}').hexdigest())
        if any(rec_index.get(key)!=value for key,value in [('target_layers_sha256',expected_target_layers),('catalog_sha256',expected_catalog),('config_sha256',expected_config),('scenario_sha256',expected_scenario),('engine_sha256',expected_engine),('probability_summaries_sha256',expected_probability_summaries)]):
            raise ValueError('Recommendation cache does not match catalog/scenario/model/engine/physics summaries. Run scripts/build_decisions.mjs first.')
        expected_shards={(nation['id'],version) for nation in catalog['nations'] for version in ('current','planned')}
        if {(item['nation'],item['version']) for item in rec_index['files']}!=expected_shards:
            raise ValueError('Static release requires both versions of every nation; this is only a partial decision build.')
        for file in rec_index['files']:
            source=local_file(rec_source,file['url'])
            if file_hash(source)!=file['sha256'] or source.stat().st_size!=file['bytes']:
                raise ValueError('Recommendation shard hash/size mismatch: '+file['url'])
            url='data/recommendations/'+file['url']
            target=web/url;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
            compressed=gzip.compress(source.read_bytes(),mtime=0)
            target.with_name(target.name+'.gz').write_bytes(compressed)
            manifest['recommendations'][f'{file["version"]}/{file["nation"]}']={key:file[key] for key in ('sha256','bytes','gzip_bytes')}
            manifest['recommendations'][f'{file["version"]}/{file["nation"]}'].update(url=url,gzip_url=url+'.gz',gzip_bytes=len(compressed))
            rec=load(web/url)
            best=rec.get('best')
            manifest['national_recommendations'].append({'nation':file['nation'],'version':file['version'],'br_caps':len(file['caps']),'best':{key:best.get(key) for key in ('actual_br','br','candidate_cap','lineup_ids','score','lower','upper','coverage','metrics')} if best else None})
        manifest['impact']=write_json(web,'data/impact/index.json',{'impact':rec_index['impact'],'audit':rec_index['audit'],'build_key':rec_index['build_key']})
        decision_report=load(rec_source/'build-report.json')
        stable_decision_report={key:value for key,value in decision_report.items() if key not in ('elapsed_ms','cache_invalidated_by','prior_build_key')}
        manifest['decision_build_report']=write_json(web,'data/reports/decision-build.json',stable_decision_report)
    else:
        raise FileNotFoundError('No nationwide precomputed decisions; run scripts/build_decisions.mjs first.')
    probability_root=probability_directory(root)
    probability_path=probability_root/'index.json'
    probability=None
    if mode!='disabled':
        if probability_path.exists():
            probability=load(probability_path)
            raw=probability_path.read_bytes()
            manifest['probability_summary']={'targets':len({e['target_id'] for e in probability['entries']}),'profiles':len(probability['entries']),'views':sum(len(e['shards']) for e in probability['entries']),'source_version':probability.get('source_version')}
        if mode=='embedded':
            if probability is None:
                raise FileNotFoundError(f'Embedded probability mode requires {probability_path}. Run the offline export first.')
            copy_probability(probability_root,data/'probability',probability)
            url='data/probability/index.json'
        else:
            url=base_url+'index.json'
        manifest['probability']={'url':url,'gzip_url':url+'.gz'}
        expected=settings['probability'].get('index_sha256')
        if expected is not None and (not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected)):
            raise ValueError('probability.index_sha256 must be a lowercase SHA-256 hex digest or null.')
        if probability is not None:
            actual=hashlib.sha256(raw).hexdigest()
            if expected and expected!=actual:
                raise ValueError('Configured probability index SHA-256 does not match the local export.')
            manifest['probability'].update(sha256=actual,bytes=len(raw))
            if mode=='embedded':
                manifest['probability']['gzip_bytes']=(data/'probability/index.json.gz').stat().st_size
        elif expected:
            manifest['probability']['sha256']=expected
    audit_path=root/'reports/source-audit.json'
    manifest['audit']=write_json(web,'data/reports/source-audit.json',load(audit_path) if audit_path.exists() else {'status':'missing_source_audit','issues':['No source audit was provided.']})
    probability_reports={}
    if catalog['snapshot'].get('client_source'):
        manifest['client_summary']=catalog['snapshot']['client_source']
    if mode!='disabled' and (probability_root/'export-report.json').exists():
        probability_reports['full_atlas_quantization']=load(probability_root/'export-report.json')
    for file in (root/'reports').glob('*.json'):
        if file.stem=='probability-release':
            # The compute-side upload inventory can contain 100,000+ objects.
            # Public audit needs version/counts, never the full file listing.
            release=load(file)
            probability_reports[file.stem]={key:release[key] for key in
                ('schema_version','release_id','index_sha256','source_version','file_count','total_bytes',
                 'entry_count','all_referenced_hashes_verified','gzip_payloads_verified','publish_prefix') if key in release}
        elif any(word in file.stem for word in ('probability','cpu-model','cpu-geometry','pipeline','export')):
            probability_reports[file.stem]=load(file)
        elif file.stem in ('client-binding-validation','client-raster-validation','client-sources','client-compute'):
            probability_reports[file.stem]=load(file)
    manifest['probability_report']=write_json(web,'data/reports/probability.json',probability_reports)
    decision_probability_path=root/'data/snapshots/decision-probabilities.json'
    if decision_probability_path.exists():
        runtime_bundle=load_runtime_bundle(decision_probability_path,expected_probability_summaries)
        manifest['decision_probabilities']=write_json(web,'data/physics/decision-probabilities.json',runtime_bundle)
    layers=load(root/'data/snapshots/target-layers.json')
    manifest['target_layers']={}
    # Load only the requested target orientation in the browser worker.
    for angle in sorted({view['angle_deg'] for target in layers['targets'] for view in target['views']}):
        shard={**layers,'targets':[{**target,'views':[view for view in target['views'] if view['angle_deg']==angle]} for target in layers['targets']]}
        manifest['target_layers'][str(angle)]=write_json(web,f'data/physics/target-layers-{angle}.json',shard)
    manifest['target_layer_summary']=load(root/'reports/target-layers.json')
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
    base_report['manifest_raw_bytes']=len(manifest_raw)
    base_report['manifest_gzip_bytes']=len(gzip.compress(manifest_raw,mtime=0))
    base_report['default_first_load_gzip_bytes']=initial_data_bytes+base_report['manifest_gzip_bytes']+core_gzip_bytes+bundled_fonts_bytes
    base_report['first_load_measurement_note']='Core/data use gzip sizes; bundled WOFF2 uses its encoded size. Browser HTTP transfer measurements are recorded separately.'
    base_report.update(validate_site(web,settings['budgets']))
    base_report['duration_s']=time.perf_counter()-started
    (root/'reports').mkdir(parents=True,exist_ok=True)
    (root/'reports/web-build.json').write_text(json.dumps(base_report,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest,base_report

def check_inputs(root: Path) -> None:
    required=('data/snapshots/catalog.json','data/snapshots/changes.json',
              'data/snapshots/target-layers.json','reports/target-layers.json')
    missing=[name for name in required if not (root/name).is_file()]
    if missing:
        raise FileNotFoundError('Missing offline inputs: '+', '.join(missing)+'. Production snapshots are not tracked in Git. See docs/DEVELOPMENT.md; prepare your snapshots before building. Tests use explicitly synthetic fixtures.')


def build(root: Path=ROOT, output: Path | None=None, probability_mode: str | None=None,
          probability_base_url: str | None=None):
    """Assemble into a fresh directory, verify budgets, then replace the site.

    Large exports are never traversed or copied unless embedded mode is explicit.
    A failed build leaves the prior deployment intact.
    """
    root=Path(root).resolve()
    check_inputs(root)
    settings=deployment_settings(root)
    mode=probability_mode or ('external' if probability_base_url else settings['probability']['mode'])
    if mode not in ('disabled','external','embedded'):
        raise ValueError('Probability mode must be disabled, external or embedded.')
    base_url=probability_base_url if probability_base_url is not None else settings['probability']['base_url']
    if mode=='external':
        base_url=normalize_base_url(base_url)
    target=site_directory(root,output)
    protected=[root/name for name in ('web','shared','backend','scripts','config','docs','tests','data','.git')]
    if root.is_relative_to(target) or any(target.is_relative_to(path) for path in protected):
        raise ValueError(f'Site output must be separate from source and data directories: {target}')
    if target.exists() and (not target.is_dir() or (any(target.iterdir()) and not (target/MARKER).is_file())):
        raise ValueError(f'Refusing to replace an unmanaged nonempty directory: {target}')
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f'.{target.name}-build-',dir=target.parent) as directory:
        work=Path(directory);staging=work/'site';staging.mkdir()
        manifest,report=_assemble(root,staging,mode,base_url,settings)
        (staging/MARKER).write_text('Generated by scripts/build_web.py; rebuild from source.\n',encoding='utf-8')
        previous=work/'previous'
        if target.exists():
            target.replace(previous)
        try:
            staging.replace(target)
        except BaseException:
            if previous.exists():
                previous.replace(target)
            raise
    report['output_directory']=str(target)
    (root/'reports/web-build.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,help='Generated site directory (default: config/hosting.json).')
    parser.add_argument('--probability-mode',choices=('disabled','external','embedded'))
    parser.add_argument('--probability-base-url',help='Immutable external data release directory; implies external mode.')
    args=parser.parse_args()
    try:
        build(args.root,args.output,args.probability_mode,args.probability_base_url)
    except (ValueError,FileNotFoundError) as error:
        parser.exit(1,str(error)+'\n')
