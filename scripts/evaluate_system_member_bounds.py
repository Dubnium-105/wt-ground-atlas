#!/usr/bin/env python3
"""Evaluate exact member maps without guessing target selection probabilities."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import file_hash,read_json,write_json
from wt_atlas.forecast.data import Atlas,safe_file
from wt_atlas.forecast.engine import ammunition_payloads
from wt_atlas.forecast.inventory import bind_geometry
from wt_atlas.forecast.system_physics import declared_members,evaluate


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('catalog','ammunition','atlas','config','bindings','out'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('attacker','ammo','system'):p.add_argument('--'+name,required=True)
    p.add_argument('--skill-profile',default='normal');a=p.parse_args()
    sources=[a.catalog,a.ammunition,a.config,a.bindings]
    if a.out.resolve() in {x.resolve() for x in sources} or a.out.resolve().is_relative_to(a.atlas.resolve()):
        raise ValueError('Output overlaps input')
    code_paths=[ROOT/p for p in ('backend/wt_atlas/forecast/system_physics.py','backend/wt_atlas/forecast/aim.py',
        'backend/wt_atlas/forecast/engine.py','backend/wt_atlas/forecast/data.py','backend/wt_atlas/forecast/inventory.py',
        'backend/wt_atlas/forecast/belts.py','backend/wt_atlas/forecast/delivery.py','backend/wt_atlas/npz_io.py',
        'backend/wt_atlas/common.py')]+[Path(__file__)]
    def code_hash(path):return hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    code_hashes={path.relative_to(ROOT).as_posix():code_hash(path) for path in code_paths}
    hashes={str(x):file_hash(x) for x in sources};catalog=read_json(a.catalog);config=read_json(a.config)
    members=declared_members(catalog,a.system);identifiers={m['vehicle_id'] for m in members}
    receipt=read_json(a.bindings);selected={**catalog,'vehicles':[v for v in catalog['vehicles'] if v['id'] in identifiers]}
    bound,errors=bind_geometry(Path(receipt['repository']),Path(receipt['jobs']),selected)
    if errors or set(bound)!=identifiers or any(receipt.get('bindings',{}).get(k,{}).get('target_sha256')!=v['target_sha256'] for k,v in bound.items()):
        raise ValueError('System geometry source verification failed')
    atlas=Atlas(a.atlas,a.ammunition,catalog);attacker=atlas.catalog.get(a.attacker)
    if not attacker or attacker.get('kind')!='ground':raise ValueError('Ground attacker missing')
    payloads=[x for x in ammunition_payloads(atlas.bindings(attacker)) if x[0].get('id')==a.ammo]
    if len(payloads)!=1:raise ValueError('Exact owned ammunition selection required')
    ammo,profiles,_=payloads[0]
    rows=[evaluate(atlas,members,bound,attacker,ammo,profiles,s,config,a.skill_profile) for s in config['scenarios']]
    used={m['map_id'] for row in rows for m in row['members'] if m.get('map_id')}
    for map_id in used:
        meta=atlas.meta[map_id]
        if (file_hash(safe_file(atlas.root,meta['array_file']))!=meta['array_sha256'] or
                file_hash(safe_file(atlas.root,'arrays/'+map_id+'.json'))!=meta['metadata_sha256']):
            raise ValueError('Physical map changed during evaluation')
    again,errors=bind_geometry(Path(receipt['repository']),Path(receipt['jobs']),selected)
    if (errors or again!=bound or any(file_hash(Path(path))!=sha for path,sha in hashes.items()) or
            any(code_hash(path)!=code_hashes[path.relative_to(ROOT).as_posix()] for path in code_paths)):
        raise ValueError('Inputs changed during evaluation')
    result={'schema_version':'1.0.0','system_id':a.system,'attacker_id':a.attacker,'ammo_id':a.ammo,
        'skill_profile':a.skill_profile,'rows':rows,'input_sha256':hashes,
        'source_code_sha256_lf':code_hashes,
        'publishable':False,'formal_rankings':0,'target_selection_weights':'unknown',
        'scope':'Conditional penetration of a selected source member under declared aim assumptions. No system-level destruction or encounter distribution is inferred.'}
    write_json(a.out,result);print(json.dumps({'scenarios':len(rows),'member_map_loads':atlas.loads,'publishable':False}))


if __name__=='__main__':main()
