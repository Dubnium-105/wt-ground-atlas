#!/usr/bin/env python3
"""Find missing native map pairs using exact ownership and verified yaw slices."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.native_rebuild import plan
from wt_atlas.common import write_json

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('catalog','ammunition','index','out'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--config',type=Path,default=ROOT/'config/forecast.json')
    a=p.parse_args();r=plan(a.catalog,a.ammunition,a.index,a.config);write_json(a.out,r)
    print(json.dumps({k:v for k,v in r.items() if k not in ('candidate_rebuild_requests','vehicles_without_exact_owned_profile')},indent=2))
if __name__=='__main__':main()
