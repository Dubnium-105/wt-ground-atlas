#!/usr/bin/env python3
"""Doctor, inventory, source binding and resumable NPZ preflight for V3."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.forecast.data import Atlas, canonical, read_json, verify_manifest
from wt_atlas.forecast.inventory import Inventory, bind_geometry, coverage, discover


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['doctor','inventory','preflight','bind'])
    p.add_argument('--data-root',type=Path,action='append',default=[])
    for key in ('catalog','ammunition','atlas','sample-root','jobs','repository','bindings'):
        p.add_argument('--'+key,type=Path)
    p.add_argument('--config',type=Path,default=ROOT/'config/forecast.json')
    p.add_argument('--index',type=Path,default=ROOT/'data/cache/forecast-inventory.sqlite')
    p.add_argument('--out',type=Path,default=ROOT/'reports/data-coverage.json')
    p.add_argument('--max-workers',type=int,default=2)
    p.add_argument('--memory-budget',type=int,default=512,help='MiB shared by array workers')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--force',action='store_true')
    p.add_argument('--limit',type=int)
    a=p.parse_args(argv)
    started=time.perf_counter()
    roots=a.data_root or [Path(os.environ.get('WT_ATLAS_DATA_ROOT',ROOT))]
    report={'schema_version':'1.0.0','command':a.command,'discovered':discover(roots),'blockers':[]}
    index=None
    try:
        if a.sample_root:
            report['manifest']=verify_manifest(a.sample_root)
            a.catalog=a.sample_root/'catalog/vehicles.sample.json'
            a.ammunition=a.sample_root/'ammunition/all_resolved_profiles_final_with_apcr_gun_caliber.json'
            a.atlas=a.sample_root/'v2'
        if a.command=='bind':
            if not all((a.catalog,a.jobs,a.repository)):raise ValueError('bind requires --catalog --jobs --repository')
            bound,errors=bind_geometry(a.repository,a.jobs,read_json(a.catalog))
            report.update(bindings=bound,errors=errors,status='verified' if not errors else 'partial')
        elif a.command!='doctor':
            if not all((a.catalog,a.ammunition,a.atlas)):raise ValueError('Select exact --catalog --ammunition --atlas; discovery never guesses matching versions')
            catalog=read_json(a.catalog)
            bindings=read_json(a.bindings).get('bindings',{}) if a.bindings else {}
            index=Inventory(a.index)
            report['inventory']=index.sync(a.atlas,a.ammunition,catalog,bindings=bindings,force=a.force)
            if a.command=='preflight':
                report['inventory']=index.verify(a.atlas,max_workers=a.max_workers,memory_budget=a.memory_budget*1024**2,resume=a.resume and not a.force,limit=a.limit)
            atlas=Atlas(a.atlas,a.ammunition,catalog,index=index)
            report.update(coverage(catalog,atlas,read_json(a.config),index))
        report['status']='blocked' if report['blockers'] else 'completed'
    except (ValueError,KeyError,OSError,TypeError) as exc:
        report['status']='blocked';report['blockers'].append(str(exc))
    finally:
        if index:index.close()
    report['duration_seconds']=time.perf_counter()-started
    a.out.parent.mkdir(parents=True,exist_ok=True)
    tmp=a.out.with_suffix('.tmp');tmp.write_text(canonical(report),encoding='utf-8');os.replace(tmp,a.out)
    print(json.dumps({'report':str(a.out),'status':report['status'],'blockers':report['blockers'],'inventory':report.get('inventory'),'duration_seconds':report['duration_seconds']},indent=2))
    return report


if __name__=='__main__':main()
