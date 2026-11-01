#!/usr/bin/env python3
"""Plan all available source-owned system-member research scenarios."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import write_json
from wt_atlas.system_batch_planning import plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('catalog','ammunition','config','bindings','out'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--index',type=Path,action='append',required=True);args=parser.parse_args()
    if args.out.resolve() in {p.resolve() for p in (args.catalog,args.ammunition,args.config,args.bindings,*args.index)}:
        raise ValueError('Planning output overlaps source')
    result=plan(args.catalog,args.ammunition,args.config,args.bindings,args.index);write_json(args.out,result)
    print(json.dumps({'candidates':len(result['requests']),'unique_maps':len(result['keys']),'publishable':False}))


if __name__=='__main__':main()
