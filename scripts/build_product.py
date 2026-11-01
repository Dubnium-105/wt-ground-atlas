#!/usr/bin/env python3
"""Compose automatic V3 predictions and the existing research workspace in one site."""
import argparse
import json
import gzip
import hashlib
import html as html_module
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.forecast.data import canonical,file_hash,read_json
from wt_atlas.system_study import build_study
from wt_atlas.system_batch_study import build_batch
from site_lib import validate_site,copy_sources,deployment_settings,local_file


def read_published_json(directory, descriptor):
    path=local_file(directory,descriptor['url'])
    opener=gzip.open if path.suffix=='.gz' else open
    with opener(path,'rb') as stream:raw=stream.read(descriptor['bytes']+1)
    if len(raw)!=descriptor['bytes'] or hashlib.sha256(raw).hexdigest()!=descriptor['sha256']:
        raise ValueError('Published source descriptor checksum')
    return json.loads(raw)


def research_catalog_identity(forecast, index, research):
    """Do not route a current prediction into a different catalog's BR rules."""
    report=read_published_json(forecast,index['report'])
    expected=report.get('inputs',{}).get('catalog')
    manifest=read_json(research/'data/manifest.json')
    identities={manifest['catalog_sha256']} if manifest.get('catalog_sha256') else set()
    # Older research sites carry the same source hash in their decision shards.
    for descriptor in manifest.get('recommendations',{}).values():
        payload=read_published_json(research,descriptor)
        identities.add(payload.get('data_provenance',{}).get('catalog_sha256'))
    if not expected or identities!={expected}:
        raise ValueError('Research and forecast catalog mismatch; rebuild research with the same --catalog input before composition')
    return {'status':'verified_same_input_catalog','catalog_sha256':expected}


def bridge_research(directory):
    """Keep the supplied research build's engine; add only the shared route contract."""
    path=directory/'app.mjs';code=path.read_text(encoding='utf-8')
    anchor="  const hash=decodeURIComponent(location.hash.slice(1)),[view,id]=hash.split('/');"
    if anchor not in code:raise ValueError('Research build lacks the supported navigation contract; rebuild research first')
    route="""  if(!navigate.predictionRouteApplied){
    navigate.predictionRouteApplied=true;const route=new URLSearchParams(location.search),change={};
    if(state.catalog.nations.some(n=>n.id===route.get('nation')))change.nation=route.get('nation');
    if(['current','planned'].includes(route.get('version')))change.version=route.get('version');
    if(route.has('br')&&Number.isFinite(Number(route.get('br'))))change.br=Number(route.get('br'));
    if(Object.keys(change).length)await switchContext(change);
  }
"""
    code=code.replace(anchor,route+anchor).replace("const available=await loadArmor();","const available=await loadArmor(id);")
    path.write_text(code,encoding='utf-8')
    # Earlier research builds omitted aircraft weapon profiles from the worker
    # projection although their offline engine consumed them. Recover exactly
    # those fields from this SAME published snapshot, never from a newer catalog.
    manifest=read_json(directory/'data/manifest.json')
    def read_descriptor(d):
        return read_published_json(directory,d)
    engine=read_descriptor(manifest['engine_catalog']);by_id={v['id']:v for v in engine['vehicles']};repaired=0
    for descriptor in manifest['nations'].values():
        for v in read_descriptor(descriptor)['vehicles']:
            item=by_id[v['id']]
            changed=False
            for field in ('weapon_profiles','air_defence_applicability'):
                if field in v and item.get(field)!=v[field]:item[field]=v[field];changed=True
                elif field not in v and field in item:del item[field];changed=True
            if changed:repaired+=1
    if repaired:
        raw=canonical(engine).encode();sha=hashlib.sha256(raw).hexdigest();name='data/catalog/engine.'+sha[:16]+'.json.gz';compressed=gzip.compress(raw,mtime=0)
        old=manifest['engine_catalog'];(directory/name).write_bytes(compressed)
        manifest['engine_catalog']={'url':name,'gzip_url':name,'sha256':sha,'bytes':len(raw),'gzip_bytes':len(compressed),'gzip_only':True}
        manifest['projection_repair']={'fields':['weapon_profiles','air_defence_applicability'],'vehicles':repaired,'source':'exact same-snapshot nation fields; no additional applicability inference','previous_engine_catalog_sha256':old['sha256']}
        for previous in {old['url'],old.get('gzip_url',old['url'])}:
            if previous!=name:local_file(directory,previous).unlink()
    replacements={};removed_bytes=0
    for d in manifest.get('recommendations',{}).values():
        if d['url']==d.get('gzip_url') or not d.get('gzip_url'):continue
        raw_path=local_file(directory,d['url']);compressed=local_file(directory,d['gzip_url'])
        data=gzip.decompress(compressed.read_bytes())
        if len(data)!=d['bytes'] or hashlib.sha256(data).hexdigest()!=d['sha256'] or raw_path.read_bytes()!=data:raise ValueError('Research recommendation compression mismatch')
        replacements[d['url']]=d['gzip_url'];removed_bytes+=raw_path.stat().st_size
    def rewrite(value):
        if isinstance(value,dict):
            if value.get('url') in replacements:value.update(url=replacements[value['url']],gzip_only=True)
            for child in value.values():rewrite(child)
        elif isinstance(value,list):
            for child in value:rewrite(child)
    rewrite(manifest)
    for name in replacements:local_file(directory,name).unlink()
    manifest['recommendation_transport']={'gzip_only':True,'decoded_hashes_unchanged':True,'duplicate_raw_bytes_removed':removed_bytes}
    encoded=canonical(manifest).encode();(directory/'data/manifest.json').write_bytes(encoded);(directory/'data/manifest.json.gz').write_bytes(gzip.compress(encoded,mtime=0))


def compose(forecast,output,research=None,system_reports=(),system_catalog=None,system_ammunition=None,system_bindings=None,system_config=None,system_batch=None,system_plan=None,system_source_root=None):
    forecast=Path(forecast).resolve();output=Path(output)
    if any(p.is_symlink() for p in (output,*output.parents)):raise ValueError('Symlink output')
    output=output.resolve()
    if system_reports and system_batch or bool(system_batch)!=bool(system_plan):raise ValueError('Choose system reports or a batch with its plan')
    if any((bool(system_reports),bool(system_batch),bool(system_catalog),bool(system_ammunition),bool(system_bindings),bool(system_config))) and not all((system_reports or system_batch,system_catalog,system_ammunition,system_bindings,system_config)):
        raise ValueError('System research requires reports, catalog, ammunition, geometry bindings and configuration')
    if (system_reports or system_batch) and any(Path(p).resolve().is_relative_to(output) for p in (*system_reports,*([system_batch,system_plan] if system_batch else []),system_catalog,system_ammunition,system_bindings,system_config)):
        raise ValueError('System research input overlaps product output')
    source_root=Path(system_source_root).resolve() if system_source_root else ROOT
    study=build_study(system_reports,system_catalog,system_ammunition,system_bindings,system_config,source_root) if system_reports else None
    if ROOT.is_relative_to(output) or any(output.is_relative_to(ROOT/p) for p in ('data','web','backend','scripts','tests','config','docs','.git')):raise ValueError('Unsafe product output')
    if forecast==output or forecast.is_relative_to(output):raise ValueError('Forecast input overlaps output')
    if research and (Path(research).resolve()==output or Path(research).resolve().is_relative_to(output)):raise ValueError('Research input overlaps output')
    if output.exists() and any(output.iterdir()) and not (output/'.wt-atlas-site').is_file():raise ValueError('Unmanaged product output')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.product-',dir=output.parent) as temporary:
        stage=Path(temporary)/'site';shutil.copytree(forecast,stage,ignore=shutil.ignore_patterns('.wt-forecast-site'))
        index=read_json(stage/'index.json')
        if system_batch:study=build_batch(system_batch,system_plan,system_catalog,system_ammunition,system_bindings,system_config,source_root,stage/'data/system-study')
        if study:
            raw=canonical(study).encode();sha=hashlib.sha256(raw).hexdigest();name='data/system-study.'+sha[:16]+'.json'
            (stage/name).write_bytes(raw)
            for filename in ('system-study.html','system-study.mjs'):shutil.copyfile(ROOT/'web/forecast'/filename,stage/filename)
            index['system_study']={'page_url':'system-study.html','source_version':study['source_version'],
                                   'data':{'url':name,'sha256':sha,'bytes':len(raw)}}
        if research:
            research=Path(research).resolve();validate_site(research)
            alignment=research_catalog_identity(forecast,index,research)
            shutil.copytree(research,stage/'research',ignore=shutil.ignore_patterns('.wt-atlas-site','forecast'))
            bridge_research(stage/'research')
            index['research']={'url':'research/index.html','status':'available','catalog_alignment':alignment,'manifest_sha256':file_hash(stage/'research/data/manifest.json'),'source_manifest_sha256':file_hash(research/'data/manifest.json')}
        else:
            copy_sources(ROOT,stage/'research')
            (stage/'research/index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>研究快照未提供</title><h1>研究快照未提供</h1><p>请使用 --research-site 组合已构建并通过校验的研究站点。</p><a href="../index.html">返回自动预测</a></html>',encoding='utf-8')
            index['research']={'url':'research/index.html','status':'offline_snapshot_missing'}
        (stage/'index.json').write_text(canonical(index),encoding='utf-8')
        for cohort in index.get('cohorts',[]):
            directory=stage/cohort['directory'];ci=read_json(directory/'index.json')
            ci['research']={**index['research'],'url':os.path.relpath(stage/'research/index.html',directory).replace('\\','/')}
            if study:ci['system_study']={**index['system_study'],'page_url':os.path.relpath(stage/'system-study.html',directory).replace('\\','/')}
            (directory/'index.json').write_text(canonical(ci),encoding='utf-8')
        if research:
            html=stage/'research/index.html';text=html.read_text(encoding='utf-8')
            research_manifest=read_json(stage/'research/data/manifest.json')
            model=html_module.escape(str(research_manifest.get('model_version','unknown')))
            banner=f'<aside class="model-note" aria-label="研究模型说明">研究模型 {model} · 条件分析分数；V3 正式推荐及数据准入状态见自动预测首页。</aside>'
            forecast_report=read_published_json(forecast,index['report'])
            if forecast_report.get('sample_subset') is True:
                count=forecast_report.get('catalog_vehicles')
                count_text=f'仅 {count} 辆载具' if type(count) is int and count>=0 else '数据范围有限'
                banner+=f'<aside class="model-note" aria-label="样例范围">样例目录：{count_text}；逐车与防护页面不代表全服，缺失物理数据不会推断。</aside>'
            text=text.replace('<body>','<body><a href="../index.html" class="predictor-return">← 自动队列预测</a>'+banner)
            html.write_text(text,encoding='utf-8')
        (stage/'.wt-atlas-site').write_text('Unified V3 predictor and legacy research\n',encoding='utf-8')
        manifest={'schema_version':'2.0.0','kind':'unified_forecast_site','forecast':'index.json','research':index['research'],
                  'build_key':index['build_key'],'deployment':{'probability_mode':read_json(stage/'research/data/manifest.json').get('deployment',{}).get('probability_mode','disabled') if research else 'disabled'}}
        (stage/'data/manifest.json').write_text(canonical(manifest),encoding='utf-8')
        report=validate_site(stage,deployment_settings(ROOT)['budgets'])
        if system_reports and build_study(system_reports,system_catalog,system_ammunition,system_bindings,system_config,source_root)!=study:raise ValueError('System research changed during composition')
        previous=output.with_name(output.name+'.previous')
        if previous.exists():
            if previous.is_symlink() or not (previous/'.wt-atlas-site').is_file() or previous.parent!=output.parent:raise ValueError('Unsafe previous publication')
            shutil.rmtree(previous)
        if output.exists():os.replace(output,previous)
        try:os.replace(stage,output)
        except BaseException:
            if previous.exists():os.replace(previous,output)
            raise
    return {**report,'output':str(output),'build_key':index['build_key'],'research':index['research']}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,add_help=False)
    p.add_argument('--output',type=Path,default=ROOT/'dist/site')
    p.add_argument('--research-site',type=Path)
    p.add_argument('--system-study-report',type=Path,action='append',default=[])
    p.add_argument('--system-study-catalog',type=Path)
    p.add_argument('--system-study-ammunition',type=Path)
    p.add_argument('--system-study-bindings',type=Path)
    p.add_argument('--system-study-config',type=Path)
    p.add_argument('--system-study-batch',type=Path)
    p.add_argument('--system-study-plan',type=Path)
    p.add_argument('--system-study-source-root',type=Path)
    p.add_argument('--forecast-output',type=Path,default=ROOT/'dist/forecast')
    a,remaining=p.parse_known_args(argv)
    # The product opens on the full task model. Explicit research/sample builds
    # retain their requested configuration and the original sample diagnostics.
    if not any(x=='--config' or x.startswith('--config=') for x in remaining) and not any(x=='--sample-root' or x.startswith('--sample-root=') for x in remaining):
        remaining=['--config',str(ROOT/'config/forecast.full-ground-rb.json'),*remaining]
    command=[sys.executable,str(ROOT/'scripts/build_forecast.py'),*remaining,'--out',str(a.forecast_output)]
    subprocess.run(command,check=True)
    if any(option in remaining for option in ('--dry-run','--help','-h')):return
    result=compose(a.forecast_output,a.output,a.research_site,a.system_study_report,a.system_study_catalog,a.system_study_ammunition,a.system_study_bindings,a.system_study_config,a.system_study_batch,a.system_study_plan,a.system_study_source_root)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
