#!/usr/bin/env python3
"""Recompute source-native KE distances from ordered layers; never rescale maps."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.range_rebuild import rebuild
def main():
    p=argparse.ArgumentParser(description=__doc__)
    for field in ('repository','catalog','ammunition','out'):p.add_argument('--'+field,type=Path,required=True)
    p.add_argument('--config',type=Path,default=ROOT/'config/pipeline.json')
    p.add_argument('--target',action='append');p.add_argument('--profile',action='append')
    p.add_argument('--distances',type=float,nargs='+',default=[100,500,1000])
    p.add_argument('--limit',type=int);p.add_argument('--resume',action='store_true')
    p.add_argument('--memory-budget',type=int,default=1536,help='Maximum decoded geometry/array size, MiB; not process RSS')
    a=p.parse_args()
    result=rebuild(a.repository,a.catalog,a.ammunition,a.config,a.out,targets=a.target,profiles=a.profile,
                   distances=a.distances,limit=a.limit,memory_budget=a.memory_budget*1024**2,resume=a.resume)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if result['status']=='blocked' else 0
if __name__=='__main__':sys.exit(main())

