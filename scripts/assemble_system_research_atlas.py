#!/usr/bin/env python3
"""Assemble exact independently verified maps for system-member research."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import file_hash,read_json
from wt_atlas.forecast.data import Atlas
from wt_atlas.forecast.delivery import delivery_issue
from wt_atlas.forecast.engine import ammunition_payloads
from wt_atlas.forecast.inventory import bind_geometry
from wt_atlas.forecast.system_physics import declared_members
from wt_atlas.system_research_atlas import assemble_subset


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('catalog','ammunition','config','bindings','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--index',type=Path,action='append',required=True)
    parser.add_argument('--attacker',required=True);parser.add_argument('--ammo',required=True)
    parser.add_argument('--system',nargs='+',required=True)
    args=parser.parse_args()
    if len(set(args.system))!=len(args.system):raise ValueError('Duplicate system')
    sources=[args.catalog,args.ammunition,args.config,args.bindings,*args.index]
    if any(p.resolve().is_relative_to(args.out.resolve()) for p in sources):raise ValueError('Research atlas overlaps input')
    hashes={str(p):file_hash(p) for p in sources[:4]}
    catalog=read_json(args.catalog);config=read_json(args.config);receipt=read_json(args.bindings)
    members=[m for system in args.system for m in declared_members(catalog,system)]
    targets={m['vehicle_id'] for m in members};selected={**catalog,'vehicles':[v for v in catalog['vehicles'] if v['id'] in targets]}
    def geometry():
        result,errors=bind_geometry(Path(receipt['repository']),Path(receipt['jobs']),selected)
        if (errors or set(result)!=targets or any(receipt.get('bindings',{}).get(k,{}).get('target_sha256')!=v['target_sha256'] for k,v in result.items())):
            raise ValueError('System geometry source verification failed')
        return result
    bound=geometry()
    class EmptyIndex:metadata={};lookup={}
    atlas=Atlas(args.catalog.parent,args.ammunition,catalog,index=EmptyIndex());attacker=atlas.catalog.get(args.attacker)
    if not attacker or attacker.get('kind')!='ground':raise ValueError('Ground attacker missing')
    payloads=[x for x in ammunition_payloads(atlas.bindings(attacker)) if x[0].get('id')==args.ammo]
    if len(payloads)!=1 or payloads[0][0].get('ordered_belt'):raise ValueError('Exactly one owned homogeneous ammunition selection required')
    profiles=payloads[0][1];distances={s['physical_distance_m'] for s in config['scenarios']};chosen={}
    for distance in distances:
        matches=[p for p in profiles if p.get('distance_m')==distance]
        if len(matches)!=1 or delivery_issue(matches[0],distance):raise ValueError('Exact source profile or delivery range missing')
        chosen[distance]=matches[0]
    keys={(target,p['name'],distance) for target in targets for distance,p in chosen.items()}
    def validate_inputs():
        if any(file_hash(Path(path))!=sha for path,sha in hashes.items()) or geometry()!=bound:
            raise ValueError('Research inputs changed during assembly')
    report=assemble_subset(args.index,keys,atlas.profiles,bound,args.out,validate_inputs=validate_inputs)
    print(json.dumps({'maps':report['maps'],'systems':len(args.system),'publishable':False}))


if __name__=='__main__':main()
