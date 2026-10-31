"""Provisional coverage from separately verified immutable index snapshots.

No arrays are read here. This audit helps plan missing work and cannot promote
an assembled atlas or replace its fresh full-array preflight.
"""
from pathlib import Path
import json
import re
import sqlite3

from .data import Atlas, number
from .inventory import coverage
from ..common import content_hash, file_hash, read_json


class CachedUnion:
    def __init__(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE maps (target TEXT,ammo TEXT,distance REAL,angles TEXT,selected INTEGER)')
        self.metadata={};self.lookup={};self.map_ids=set()

    def summary(self):
        count=len(self.lookup)
        return {'selected_maps':count,'verified_arrays':count,'ambiguous_maps':0,
                'verification_basis':'previously_verified_index_snapshots_only'}

    def close(self):self.db.close()


def _snapshot(path):
    path=Path(path).resolve();wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Index has a live WAL; use a completed stable preflight: '+str(path))
    return file_hash(path)


def audit(catalog_path,ammunition_path,config_path,index_paths):
    """Join exact selected identities; return planning evidence, never release readiness."""
    paths=[Path(p).resolve() for p in index_paths]
    if not paths or len(set(paths))!=len(paths):raise ValueError('Distinct source indexes required')
    input_paths=[Path(p).resolve() for p in (catalog_path,ammunition_path,config_path)]
    if len(set(input_paths+paths))!=len(input_paths)+len(paths):raise ValueError('Coverage inputs must be distinct')
    input_hashes={str(p):file_hash(p) for p in input_paths}
    index_hashes={str(p):_snapshot(p) for p in paths}
    catalog,profiles,config=(read_json(p) for p in input_paths)
    snapshot=catalog.get('snapshot',{})
    expected_version=snapshot.get('datamine_game_version') or snapshot.get('game_version') or snapshot.get('client_source',{}).get('version')
    expected_commit=snapshot.get('datamine_reference') or snapshot.get('datamine_commit')
    by_profile={p['name']:p for p in profiles};by_target={v['id']:v for v in catalog['vehicles']}
    if len(by_profile)!=len(profiles) or len(by_target)!=len(catalog['vehicles']):raise ValueError('Duplicate catalog or ammunition identity')
    hashes={name:content_hash(profile) for name,profile in by_profile.items()}
    union=CachedUnion();sources=[];versions=set()
    try:
        for path in paths:
            db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
            try:
                if db.execute("SELECT count(*) FROM maps WHERE selected=1 AND status!='verified'").fetchone()[0]:
                    raise ValueError('Source index has unverified selections: '+str(path))
                root=db.execute("SELECT value FROM settings WHERE key='root'").fetchone()
                if not root:raise ValueError('Source index lacks its atlas root')
                count=0;batch=[]
                rows=db.execute("SELECT id,target,ammo,distance,angles,metadata,meta_hash,array_stamp FROM maps WHERE selected=1")
                for map_id,target,ammo,distance,angles,raw,meta_hash,array_stamp in rows:
                    if not all(isinstance(v,str) and v for v in (map_id,target,ammo,raw,meta_hash,array_stamp)) or not number(distance):
                        raise ValueError('Incomplete verified source index identity')
                    meta=json.loads(raw);profile=by_profile.get(ammo);vehicle=by_target.get(target)
                    try:stamp=json.loads(array_stamp)
                    except (TypeError,ValueError) as error:raise ValueError('Invalid cached array stamp') from error
                    if (not re.fullmatch('[0-9a-f]{64}',meta_hash) or meta.get('metadata_sha256')!=meta_hash or
                        not isinstance(stamp,list) or len(stamp)!=3 or any(type(x) is not int or x<0 for x in stamp)):
                        raise ValueError('Invalid cached source fingerprints')
                    if profile is None or vehicle is None:raise ValueError('Index identity absent from selected catalog or ammunition')
                    prov=profile.get('provenance',{});client=vehicle.get('client_collision') or vehicle.get('source_attributes',{}).get('client',{})
                    if (meta.get('map_id')!=map_id or meta.get('target_id')!=target or meta.get('ammo_id')!=ammo or
                        meta.get('ammo_profile_sha256')!=hashes[ammo] or profile.get('distance_m')!=distance or
                        not isinstance(meta.get('source_version'),str) or not meta['source_version'] or
                        not isinstance(meta.get('datamine_commit'),str) or not meta['datamine_commit'] or
                        (expected_version and meta['source_version']!=expected_version) or
                        (expected_commit and meta['datamine_commit']!=expected_commit) or
                        meta.get('source_version')!=prov.get('client_version') or meta.get('datamine_commit')!=prov.get('datamine_commit') or
                        client.get('source_version')!=meta.get('source_version') or
                        meta.get('target_binding')!='independent_geometry_verified' or
                        (vehicle.get('analysis_target_sha256') and vehicle['analysis_target_sha256']!=meta.get('target_sha256'))):
                        raise ValueError('Index metadata conflicts with exact source identities')
                    versions.add((meta['source_version'],meta['datamine_commit']))
                    key=(target,ammo,distance)
                    if key in union.lookup or map_id in union.map_ids:raise ValueError('Duplicate physical identity across indexes')
                    union.lookup[key]=map_id;union.map_ids.add(map_id)
                    batch.append((target,ammo,distance,angles,1));count+=1
                    if len(batch)==4096:
                        union.db.executemany('INSERT INTO maps VALUES (?,?,?,?,?)',batch);batch=[]
                if batch:union.db.executemany('INSERT INTO maps VALUES (?,?,?,?,?)',batch)
                sources.append({'index_sha256':index_hashes[str(path)],'verified_selections':count})
            finally:db.close()
        if len(versions)!=1 or not union.lookup:raise ValueError('Mixed or empty physical source versions')
        union.db.commit()
        atlas=Atlas(paths[0].parent,input_paths[1],catalog,index=union)
        result=coverage(catalog,atlas,config,union)
        if any(file_hash(Path(p))!=h for p,h in input_hashes.items()) or any(_snapshot(p)!=index_hashes[str(p)] for p in paths):
            raise ValueError('Coverage input changed during audit')
        result.update({'scope':'Provisional coverage planning from previously verified index snapshots; source arrays and assembled atlas are not rechecked here. The matrix uses base catalog BR and does not establish unlocked or dynamic-mount eligibility.',
            'source_indexes':sources,'input_sha256':{'catalog':input_hashes[str(input_paths[0])],
            'ammunition':input_hashes[str(input_paths[1])],'config':input_hashes[str(input_paths[2])]},
            'physical_source':list(next(iter(versions))),'source_arrays_rechecked':False,
            'formal_rankings':0,'publishable':False})
        return result
    finally:union.close()
