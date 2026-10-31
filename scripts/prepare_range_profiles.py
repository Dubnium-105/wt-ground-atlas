#!/usr/bin/env python3
"""Build source-backed range profiles; no probability maps are reused or made."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import read_json,write_json,file_hash
from wt_atlas.range_rebuild import source_range_profile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ammunition',type=Path,required=True)
    parser.add_argument('--source-root',type=Path,help='Captured aces BLK tree; required for CE')
    parser.add_argument('--distances',type=float,nargs='+',default=[10,100,500,1000])
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if len({p.resolve() for p in (args.ammunition,args.out,args.report)})!=3:
        raise ValueError('Keep source, output bundle and report separate')
    original=read_json(args.ammunition)
    if not isinstance(original,list) or len({p['name'] for p in original})!=len(original):
        raise ValueError('Unique source profiles required')
    bundle=list(original);rejections=[];counts=Counter()
    for profile in original:
        for distance in sorted(set(args.distances)):
            if profile.get('distance_m')==distance:continue
            try:
                bundle.append(source_range_profile(profile,distance,args.source_root))
                counts[profile['mode']]+=1
            except ValueError as error:
                rejections.append({'profile':profile['name'],'distance_m':distance,'error':str(error)})
    write_json(args.out,bundle)
    report={'schema_version':'1.0.0','source_ammunition_sha256':file_hash(args.ammunition),
            'derived_by_mode':dict(counts),'source_profiles':len(original),'bundle_profiles':len(bundle),
            'bundle_sha256':file_hash(args.out),'rejections':rejections,'computed_maps':0,
            'endpoint':'conditional_penetration','game_probability_validated':False}
    write_json(args.report,report)
    print(json.dumps({**report,'rejections':len(rejections)},indent=2))


if __name__=='__main__':main()
