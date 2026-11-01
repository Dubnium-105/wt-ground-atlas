#!/usr/bin/env python3
"""Resume and atomically assemble all maps in a verified system research plan."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import file_hash,read_json,write_json
from wt_atlas.forecast.data import Atlas,safe_file
from wt_atlas.forecast.inventory import bind_geometry
from wt_atlas.system_batch_planning import validate_plan
from wt_atlas.system_research_atlas import assemble_subset,index_snapshot


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','catalog','ammunition','config','bindings','out','report'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--index',type=Path,action='append',required=True);parser.add_argument('--resume',action='store_true')
    a=parser.parse_args();sources=[p.resolve() for p in (a.catalog,a.ammunition,a.config,a.bindings)]
    if (a.out.resolve().is_relative_to(a.report.resolve()) or a.report.resolve().is_relative_to(a.out.resolve()) or
        a.report.resolve() in {p.resolve() for p in (a.plan,*sources,*a.index)} or a.report.exists()):
        raise ValueError('Research assembly report overlaps input or already exists')
    plan_sha=file_hash(a.plan);plan=read_json(a.plan)
    if plan.get('schema_version')!='1.0.0' or plan.get('publishable') is not False or plan.get('formal_rankings')!=0:
        raise ValueError('Invalid research plan')
    hashes={str(p):file_hash(p) for p in sources}
    if hashes!=plan.get('input_sha256'):raise ValueError('Research plan source identity changed')
    snapshots=[index_snapshot(p.resolve()) for p in a.index]
    if snapshots!=[row['index_sha256'] for row in plan.get('source_indexes',[])]:raise ValueError('Research plan index identity changed')
    def code_hash(path):return hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    for name,sha in plan.get('code_sha256_lf',{}).items():
        if code_hash(safe_file(ROOT,name))!=sha:raise ValueError('Research plan code identity changed')
    if not plan.get('code_sha256_lf'):raise ValueError('Research plan code identity missing')
    catalog=read_json(a.catalog);config=read_json(a.config);receipt=read_json(a.bindings)
    class EmptyIndex:metadata={};lookup={}
    atlas=Atlas(a.catalog.parent,a.ammunition,catalog,index=EmptyIndex());keys=validate_plan(plan,catalog,atlas,config)
    targets={key[0] for key in keys};selected={**catalog,'vehicles':[v for v in catalog['vehicles'] if v['id'] in targets]}
    def geometry():
        bound,errors=bind_geometry(Path(receipt['repository']),Path(receipt['jobs']),selected)
        if errors or set(bound)!=targets or any(receipt.get('bindings',{}).get(k,{}).get('target_sha256')!=v['target_sha256'] for k,v in bound.items()):
            raise ValueError('System geometry source verification failed')
        return bound
    bound=geometry()
    def validate_inputs():
        if (file_hash(a.plan)!=plan_sha or any(file_hash(p)!=hashes[str(p)] for p in sources) or geometry()!=bound or
            any(code_hash(safe_file(ROOT,name))!=sha for name,sha in plan['code_sha256_lf'].items())):
            raise ValueError('Research assembly inputs changed')
    result=assemble_subset(a.index,keys,atlas.profiles,bound,a.out,validate_inputs=validate_inputs,resume=a.resume)
    report={'schema_version':'1.0.0','plan_sha256':plan_sha,'atlas_manifest_sha256':file_hash(a.out/'.wt-system-research-atlas.json'),
            'maps':result['maps'],'candidates':len(plan['requests']),'status':result['status'],'publishable':False,'formal_rankings':0}
    write_json(a.report,report);print(json.dumps(report))


if __name__=='__main__':main()
