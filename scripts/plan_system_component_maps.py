#!/usr/bin/env python3
"""Plan missing source-owned physical maps for system member targets."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import write_json
from wt_atlas.component_native_planning import plan


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('catalog','ammunition','config','bindings','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--index',type=Path,action='append',default=[])
    a=p.parse_args()
    if a.out.resolve() in {x.resolve() for x in [a.catalog,a.ammunition,a.config,a.bindings,*a.index]}:
        raise ValueError('Output overlaps source')
    result=plan(a.catalog,a.ammunition,a.config,a.bindings,a.index);write_json(a.out,result)
    print(json.dumps({'components':len(result['target_components']),
        'candidate_request_count':result['candidate_request_count'],'counts':result['counts']}))


if __name__=='__main__':main()
