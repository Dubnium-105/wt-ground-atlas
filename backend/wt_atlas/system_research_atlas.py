"""Assemble a small source-verified research atlas from completed indexes.

This does not establish global coverage or publishability. Each copied array is
independently rehashed; the evaluator still validates its contents and geometry.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from .common import content_hash, file_hash, write_json
from .forecast.data import safe_file
from .forecast.inventory import stamp


def index_snapshot(path):
    wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:raise ValueError('Input index has an active WAL')
    return file_hash(path)


def assemble_subset(indexes, keys, profiles, bindings, output, *, validate_inputs=None):
    indexes=[Path(p).resolve() for p in indexes]; output=Path(output).resolve(); keys=set(keys)
    if not keys or not indexes or len(set(indexes))!=len(indexes):raise ValueError('Missing or duplicate research inputs')
    if output.exists():raise ValueError('Research atlas output already exists')
    before={str(p):index_snapshot(p) for p in indexes}; found={}; receipts=[]
    for index in indexes:
        with closing(sqlite3.connect(index.as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute("SELECT count(*) FROM maps WHERE selected=1 AND status!='verified'").fetchone()[0]:
                raise ValueError('Source index has unverified selections')
            root_row=db.execute("SELECT value FROM settings WHERE key='root'").fetchone()
            if not root_row:raise ValueError('Source atlas root missing')
            root=Path(root_row[0]).resolve()
            if output.is_relative_to(root) or root.is_relative_to(output) or index.is_relative_to(output):
                raise ValueError('Research atlas overlaps a source')
            n=0
            for target,ammo,distance in sorted(keys):
                rows=db.execute("SELECT id,path,metadata,meta_hash,array_stamp FROM maps WHERE target=? AND ammo=? AND distance=? AND selected=1 AND status='verified'",(target,ammo,distance)).fetchall()
                for map_id,path,raw,meta_hash,array_stamp in rows:
                    key=(target,ammo,distance)
                    if key in found:raise ValueError('Duplicate selected physical identity')
                    meta=json.loads(raw);profile=profiles.get(ammo);binding=bindings.get(target)
                    if profile is None or binding is None:raise ValueError('Profile or geometry binding missing')
                    source_meta=safe_file(root,'arrays/'+path);source_array=safe_file(root,meta['array_file'])
                    original=json.loads(source_meta.read_text(encoding='utf8'))
                    fields=('map_id','target_id','target_sha256','ammo_id','ammo_profile_sha256','source_version','datamine_commit','array_file','array_sha256')
                    prov=profile.get('provenance',{})
                    if (any(meta.get(f)!=original.get(f) for f in fields) or meta.get('map_id')!=map_id or
                        meta.get('target_id')!=target or meta.get('ammo_id')!=ammo or
                        profile.get('distance_m')!=distance or profile.get('reference_only') or
                        meta.get('target_sha256')!=binding.get('target_sha256') or
                        meta.get('target_binding')!='independent_geometry_verified' or
                        meta.get('ammo_profile_sha256')!=content_hash(profile) or
                        (meta.get('source_version'),meta.get('datamine_commit'))!=(prov.get('client_version'),prov.get('datamine_commit')) or
                        meta.get('array_file')!='arrays/'+map_id+'.npz' or path!=map_id+'.json' or
                        file_hash(source_meta)!=meta_hash or stamp(source_array)!=array_stamp or
                        file_hash(source_array)!=meta.get('array_sha256')):
                        raise ValueError('Research map identity or source changed')
                    found[key]={'metadata':source_meta,'array':source_array,'metadata_sha256':meta_hash,
                                'array_sha256':meta['array_sha256'],'map_id':map_id,'source_index_sha256':before[str(index)]};n+=1
            receipts.append({'index_sha256':before[str(index)],'selected_maps':n})
    if set(found)!=keys:raise ValueError('Missing verified research maps: '+str(len(keys-set(found))))
    if len({v['map_id'] for v in found.values()})!=len(found):raise ValueError('Duplicate map ID')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.system-research-',dir=output.parent) as temporary:
        stage=Path(temporary)/'atlas';(stage/'arrays').mkdir(parents=True)
        records=[]
        for key,item in sorted(found.items()):
            metadata=stage/'arrays'/(item['map_id']+'.json');array=stage/'arrays'/(item['map_id']+'.npz')
            shutil.copyfile(item['metadata'],metadata);shutil.copyfile(item['array'],array)
            if file_hash(metadata)!=item['metadata_sha256'] or file_hash(array)!=item['array_sha256']:
                raise ValueError('Copied research map checksum mismatch')
            records.append({'target_id':key[0],'profile_id':key[1],'distance_m':key[2],
                'map_id':item['map_id'],'metadata_sha256':item['metadata_sha256'],
                'array_sha256':item['array_sha256'],'source_index_sha256':item['source_index_sha256']})
        if any(index_snapshot(index)!=before[str(index)] for index in indexes):raise ValueError('Source index changed during assembly')
        if any(file_hash(item['metadata'])!=item['metadata_sha256'] or file_hash(item['array'])!=item['array_sha256'] for item in found.values()):
            raise ValueError('Source map changed during assembly')
        if validate_inputs is not None:validate_inputs()
        report={'schema_version':'1.0.0','status':'assembled_research_requires_fresh_evaluation',
                'maps':len(records),'source_indexes':receipts,'records':records,'publishable':False,'formal_rankings':0}
        write_json(stage/'.wt-system-research-atlas.json',report)
        os.rename(stage,output)
    return report
