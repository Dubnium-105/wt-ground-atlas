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


def prepare(original, distances, source_root, *, previous=None, existing=None):
    def indexed(profiles):
        if not isinstance(profiles,list) or len({p['name'] for p in profiles})!=len(profiles):
            raise ValueError('Unique source profiles required')
        return {p['name']:p for p in profiles}
    current=indexed(original)
    if (previous is None)!=(existing is None):
        raise ValueError('Previous source and existing ranges must be supplied together')
    if previous is None:
        bundle=list(original); candidates=original; preserved=0
    else:
        prior=indexed(previous);available=indexed(existing)
        if any(current.get(name)!=p or available.get(name)!=p for name,p in prior.items()):
            raise ValueError('Previous source profiles changed or are absent from existing ranges')
        candidates=[p for p in original if p['name'] not in prior]
        if any(p['name'] in available for p in candidates):
            raise ValueError('New source profile conflicts with existing range bundle')
        bundle=list(existing)+candidates;preserved=len(existing)
    names={p['name'] for p in bundle};rejections=[];counts=Counter()
    for profile in candidates:
        for distance in sorted(set(distances)):
            if profile.get('distance_m')==distance:continue
            try:
                derived=source_range_profile(profile,distance,source_root)
            except ValueError as error:
                rejections.append({'profile':profile['name'],'distance_m':distance,'error':str(error)})
                continue
            if derived['name'] in names:raise ValueError('Derived profile identity conflicts with existing bundle')
            names.add(derived['name']);bundle.append(derived);counts[profile['mode']]+=1
    return bundle,counts,rejections,preserved


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ammunition',type=Path,required=True)
    parser.add_argument('--source-root',type=Path,help='Captured aces BLK tree; required for CE')
    parser.add_argument('--previous-ammunition',type=Path,help='Previous source bundle for an incremental build')
    parser.add_argument('--existing-ranges',type=Path,help='Previously verified range bundle to preserve')
    parser.add_argument('--distances',type=float,nargs='+',default=[10,100,500,1000])
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    paths=[p for p in (args.ammunition,args.previous_ammunition,args.existing_ranges,args.out,args.report) if p]
    if len({p.resolve() for p in paths})!=len(paths):
        raise ValueError('Keep source, output bundle and report separate')
    original=read_json(args.ammunition)
    previous=read_json(args.previous_ammunition) if args.previous_ammunition else None
    existing=read_json(args.existing_ranges) if args.existing_ranges else None
    bundle,counts,rejections,preserved=prepare(original,args.distances,args.source_root,previous=previous,existing=existing)
    write_json(args.out,bundle)
    report={'schema_version':'1.0.0','source_ammunition_sha256':file_hash(args.ammunition),
            'derived_by_mode':dict(counts),'source_profiles':len(original),'bundle_profiles':len(bundle),
            'preserved_range_bundle_profiles':preserved,
            'previous_source_sha256':file_hash(args.previous_ammunition) if args.previous_ammunition else None,
            'existing_ranges_sha256':file_hash(args.existing_ranges) if args.existing_ranges else None,
            'bundle_sha256':file_hash(args.out),'rejections':rejections,'computed_maps':0,
            'endpoint':'conditional_penetration','game_probability_validated':False}
    write_json(args.report,report)
    print(json.dumps({**report,'rejections':len(rejections)},indent=2))


if __name__=='__main__':main()
