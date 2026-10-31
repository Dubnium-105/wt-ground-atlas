#!/usr/bin/env python3
"""Compute explicit owned native-distance pairs from independently bound layers."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.native_rebuild import rebuild

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('repository','catalog','ammunition','requests','out'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--backend',choices=['cpu','v2-cuda'],default='cpu')
    p.add_argument('--config',type=Path);p.add_argument('--resume',action='store_true');p.add_argument('--limit',type=int)
    p.add_argument('--memory-budget',type=int,default=1536,help='Maximum decoded geometry/validation array MiB; not peak RSS')
    a=p.parse_args();r=rebuild(a.repository,a.catalog,a.ammunition,a.requests,a.out,backend=a.backend,
        config_path=a.config,resume=a.resume,limit=a.limit,memory_budget=a.memory_budget*1024**2)
    print(json.dumps({k:v for k,v in r.items() if k!='maps'},indent=2))
if __name__=='__main__':main()
