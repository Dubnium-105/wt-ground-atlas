#!/usr/bin/env python3
"""Compose automatic V3 predictions and the existing research workspace in one site."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.forecast.data import canonical,file_hash,read_json
from site_lib import validate_site,copy_sources,deployment_settings


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


def compose(forecast,output,research=None):
    forecast=Path(forecast).resolve();output=Path(output)
    if any(p.is_symlink() for p in (output,*output.parents)):raise ValueError('Symlink output')
    output=output.resolve()
    if ROOT.is_relative_to(output) or any(output.is_relative_to(ROOT/p) for p in ('data','web','backend','scripts','tests','config','docs','.git')):raise ValueError('Unsafe product output')
    if forecast==output or forecast.is_relative_to(output):raise ValueError('Forecast input overlaps output')
    if research and (Path(research).resolve()==output or Path(research).resolve().is_relative_to(output)):raise ValueError('Research input overlaps output')
    if output.exists() and any(output.iterdir()) and not (output/'.wt-atlas-site').is_file():raise ValueError('Unmanaged product output')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.product-',dir=output.parent) as temporary:
        stage=Path(temporary)/'site';shutil.copytree(forecast,stage,ignore=shutil.ignore_patterns('.wt-forecast-site'))
        index=read_json(stage/'index.json')
        if research:
            research=Path(research).resolve();validate_site(research)
            shutil.copytree(research,stage/'research',ignore=shutil.ignore_patterns('.wt-atlas-site','forecast'))
            bridge_research(stage/'research')
            index['research']={'url':'research/index.html','status':'available','manifest_sha256':file_hash(research/'data/manifest.json')}
        else:
            copy_sources(ROOT,stage/'research')
            (stage/'research/index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>研究快照未提供</title><h1>研究快照未提供</h1><p>请使用 --research-site 组合已构建并通过校验的研究站点。</p><a href="../index.html">返回自动预测</a></html>',encoding='utf-8')
            index['research']={'url':'research/index.html','status':'offline_snapshot_missing'}
        (stage/'index.json').write_text(canonical(index),encoding='utf-8')
        for cohort in index.get('cohorts',[]):
            directory=stage/cohort['directory'];ci=read_json(directory/'index.json')
            ci['research']={**index['research'],'url':os.path.relpath(stage/'research/index.html',directory).replace('\\','/')}
            (directory/'index.json').write_text(canonical(ci),encoding='utf-8')
        if research:
            html=stage/'research/index.html';text=html.read_text(encoding='utf-8')
            text=text.replace('<body>','<body><a href="../index.html" class="predictor-return">← 自动队列预测</a>')
            html.write_text(text,encoding='utf-8')
        (stage/'.wt-atlas-site').write_text('Unified V3 predictor and legacy research\n',encoding='utf-8')
        manifest={'schema_version':'2.0.0','kind':'unified_forecast_site','forecast':'index.json','research':index['research'],
                  'build_key':index['build_key'],'deployment':{'probability_mode':'external' if research else 'disabled'}}
        (stage/'data/manifest.json').write_text(canonical(manifest),encoding='utf-8')
        report=validate_site(stage,deployment_settings(ROOT)['budgets'])
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
    p.add_argument('--forecast-output',type=Path,default=ROOT/'dist/forecast')
    a,remaining=p.parse_known_args(argv)
    command=[sys.executable,str(ROOT/'scripts/build_forecast.py'),*remaining,'--out',str(a.forecast_output)]
    subprocess.run(command,check=True)
    if '--dry-run' in remaining:return
    result=compose(a.forecast_output,a.output,a.research_site)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
