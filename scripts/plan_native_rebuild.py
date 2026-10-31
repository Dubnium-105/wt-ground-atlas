#!/usr/bin/env python3
"""Find missing native map pairs using exact ownership and verified yaw slices."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.native_rebuild import plan
from wt_atlas.ordered_belt_planning import plan as ordered_plan, include_components
from wt_atlas.common import write_json

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('catalog','ammunition','index','out'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--config',type=Path,default=ROOT/'config/forecast.json')
    p.add_argument('--ordered-belts-only',action='store_true',help='Plan only complete ordered selections; default also includes general missing pairs')
    p.add_argument('--profiles',nargs='+',help='Plan all missing maps for these exact profiles, including when another owned ammunition already has a map')
    a=p.parse_args()
    if a.profiles:
        if a.ordered_belts_only: p.error('--profiles and --ordered-belts-only are separate planning scopes')
        r=plan(a.catalog,a.ammunition,a.index,a.config,profile_names=a.profiles)
    else:
        ordered=ordered_plan(a.catalog,a.ammunition,a.index,a.config)
        r=ordered if a.ordered_belts_only else include_components(plan(a.catalog,a.ammunition,a.index,a.config),ordered)
    write_json(a.out,r)
    print(json.dumps({k:v for k,v in r.items() if k not in ('candidate_rebuild_requests','vehicles_without_exact_owned_profile')},indent=2))
if __name__=='__main__':main()
