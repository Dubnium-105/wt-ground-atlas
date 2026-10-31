#!/usr/bin/env python3
"""Atomically assemble verified selections and new native maps without altering sources.

Local same-volume files use hard links. This is an offline working atlas: source
mutation is visible through a hard link and must invalidate the next preflight.
Use --copy for independent bytes. Assembly never claims production readiness.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import read_json,write_json,file_hash
from wt_atlas.forecast.data import safe_file,number
from wt_atlas.forecast.inventory import stamp,verify_array


def assemble(index_path,ammunition_path,extras,output,*,copy=False):
    output=Path(output)
    if any(p.is_symlink() for p in (output,*output.parents)):raise ValueError('Symlink assembly output')
    output=output.resolve()
    if output.exists():raise ValueError('Use a new assembly directory')
    db=sqlite3.connect(Path(index_path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        base=Path(db.execute("SELECT value FROM settings WHERE key='root'").fetchone()[0]).resolve()
        sources=[base,*[Path(p).resolve() for p in extras]]
        if any(output.is_relative_to(p) or p.is_relative_to(output) for p in sources):raise ValueError('Assembly overlaps source atlas')
        if Path(index_path).resolve().is_relative_to(output) or Path(ammunition_path).resolve().is_relative_to(output):raise ValueError('Assembly overlaps input')
        if db.execute("SELECT count(*) FROM maps WHERE selected=1 AND status!='verified'").fetchone()[0]:raise ValueError('Base arrays need preflight')
        profiles={p['name']:p for p in read_json(ammunition_path)}
        output.parent.mkdir(parents=True,exist_ok=True)
        seen=set();ids=set();version=None;count=added=links=0
        with tempfile.TemporaryDirectory(prefix='.native-assembly-',dir=output.parent) as temporary:
            stage=Path(temporary)/'atlas';(stage/'arrays').mkdir(parents=True)
            def include(root,meta_path,meta,expected_hash=None,expected_stamp=None,is_extra=False):
                nonlocal count,added,links,version
                if expected_hash and file_hash(meta_path)!=expected_hash:raise ValueError('Base metadata changed; rerun preflight')
                source=safe_file(root,meta['array_file'])
                if expected_stamp and stamp(source)!=expected_stamp:raise ValueError('Base NPZ changed; rerun preflight')
                if is_extra and verify_array((str(root),meta,1536*1024**2))['status']!='verified':raise ValueError('Extra NPZ failed validation')
                profile=profiles.get(meta['ammo_id'],{})
                distance=profile.get('distance_m')
                if not number(distance):raise ValueError('Missing exact profile distance')
                key=(meta['target_id'],meta['ammo_id'],distance)
                if key in seen or meta['map_id'] in ids:raise ValueError('Duplicate physical identity in assembly')
                pair=(meta['source_version'],meta['datamine_commit'])
                if version is not None and pair!=version:raise ValueError('Mixed physical source versions')
                version=pair;seen.add(key);ids.add(meta['map_id'])
                destination=stage/'arrays'/(meta_path.stem+'.npz')
                if meta['array_file']!='arrays/'+destination.name or meta['map_id']!=meta_path.stem:
                    raise ValueError('Assembly requires canonical map paths')
                if copy:shutil.copyfile(source,destination)
                else:
                    try:os.link(source,destination);links+=1
                    except OSError as error:
                        if error.errno!=18:raise
                        shutil.copyfile(source,destination)
                shutil.copyfile(meta_path,destination.with_suffix('.json'))
                count+=1;added+=int(is_extra)
            for path,raw,meta_hash,array_stamp in db.execute("SELECT path,metadata,meta_hash,array_stamp FROM maps WHERE selected=1 AND status='verified' ORDER BY id"):
                meta=json.loads(raw);source_meta=safe_file(base,'arrays/'+path)
                include(base,source_meta,meta,meta_hash,array_stamp)
            for root in sources[1:]:
                for path in sorted((root/'arrays').glob('*.json')):
                    if path.is_symlink():raise ValueError('Symlink extra metadata')
                    include(root,path,read_json(path),is_extra=True)
            if not count:raise ValueError('Empty assembly')
            shutil.copyfile(ammunition_path,stage/'ammunition.json')
            report={'status':'assembled_requires_fresh_preflight','maps':count,'new_maps':added,'physical_source':list(version),
                'hardlinked_arrays':links,'copied_arrays':count-links,'source_files_modified':False,
                'shared_bytes_require_hash_invalidation':bool(links),'ammunition_sha256':file_hash(ammunition_path),
                'base_index':str(Path(index_path).resolve()),'source_atlases':[str(p) for p in sources]}
            write_json(stage/'.wt-assembled-atlas',report)
            os.rename(stage,output)
            return report
    finally:db.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('base-index','ammunition','out'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--extra',type=Path,action='append',default=[]);p.add_argument('--copy',action='store_true')
    a=p.parse_args();print(json.dumps(assemble(a.base_index,a.ammunition,a.extra,a.out,copy=a.copy),indent=2))
if __name__=='__main__':main()
