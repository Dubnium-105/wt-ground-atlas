#!/usr/bin/env python3
"""Atomically assemble verified selections and new native maps without altering sources.

Local same-volume files use hard links. This is an offline working atlas: source
mutation is visible through a hard link and must invalidate the next preflight.
Use --copy for independent bytes. Assembly never claims production readiness.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import read_json,write_json,file_hash
from wt_atlas.forecast.data import safe_file,number
from wt_atlas.forecast.inventory import stamp,verify_array


@contextmanager
def assembly_stage(output, identity, resume):
    """Keep an identifiable partial stage only when resumability is requested."""
    if not resume:
        with tempfile.TemporaryDirectory(prefix='.native-assembly-',dir=output.parent) as temporary:
            stage=Path(temporary)/'atlas';stage.mkdir()
            yield stage
        return
    stage=output.parent/('.'+output.name+'.partial')
    marker=stage/'.wt-assembly-stage.json'
    if stage.exists():
        if stage.is_symlink() or not marker.is_file() or read_json(marker)!=identity:
            raise ValueError('Existing partial assembly does not match these inputs')
    else:
        stage.mkdir()
        write_json(marker,identity)
    yield stage


def assemble(index_path,ammunition_path,extras,output,*,copy=False,extra_indexes=(),resume=False):
    output=Path(output)
    if any(p.is_symlink() for p in (output,*output.parents)):raise ValueError('Symlink assembly output')
    output=output.resolve()
    if output.exists():raise ValueError('Use a new assembly directory')
    indexes=[Path(p).resolve() for p in (index_path,*extra_indexes)]
    if len(set(indexes))!=len(indexes):raise ValueError('Duplicate source index')
    if resume and extras:raise ValueError('Resumable assembly requires verified extra indexes')
    databases=[]
    try:
        for path in indexes:
            db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);databases.append(db)
        roots=[Path(db.execute("SELECT value FROM settings WHERE key='root'").fetchone()[0]).resolve() for db in databases]
        sources=[*roots,*[Path(p).resolve() for p in extras]]
        if len(set(sources))!=len(sources):raise ValueError('Duplicate source atlas')
        if any(output.is_relative_to(p) or p.is_relative_to(output) for p in sources):raise ValueError('Assembly overlaps source atlas')
        if any(p.is_relative_to(output) for p in indexes) or Path(ammunition_path).resolve().is_relative_to(output):raise ValueError('Assembly overlaps input')
        for i,db in enumerate(databases):
            if db.execute("SELECT count(*) FROM maps WHERE selected=1 AND status!='verified'").fetchone()[0]:
                raise ValueError('Base arrays need preflight' if i==0 else 'Extra indexed arrays need preflight')
        profile_rows=read_json(ammunition_path)
        profiles={p['name']:p for p in profile_rows}
        if len(profiles)!=len(profile_rows):raise ValueError('Duplicate ammunition profile')
        output.parent.mkdir(parents=True,exist_ok=True)
        def wal_hash(path):
            wal=Path(str(path)+'-wal')
            return file_hash(wal) if wal.is_file() and wal.stat().st_size else None
        identity={'schema_version':'1.0.0','indexes':[{'path':str(p),'sha256':file_hash(p),
            'wal_sha256':wal_hash(p)} for p in indexes], 'assembler_sha256':file_hash(Path(__file__)),
            'sources':[str(p) for p in sources],'ammunition_sha256':file_hash(ammunition_path),'copy':copy}
        seen=set();ids=set();version=None;count=added=links=reused=0
        started=time.perf_counter()
        with assembly_stage(output,identity,resume) as stage:
            (stage/'arrays').mkdir(exist_ok=True)
            def copy_atomically(source,destination):
                partial=destination.with_name(destination.name+'.part')
                if partial.is_symlink():raise ValueError('Symlink staged partial file')
                shutil.copyfile(source,partial)
                os.replace(partial,destination)
            def include(root,meta_path,meta,expected_hash=None,expected_stamp=None,is_extra=False):
                nonlocal count,added,links,reused,version
                if expected_hash and file_hash(meta_path)!=expected_hash:raise ValueError('Indexed metadata changed; rerun preflight')
                source=safe_file(root,meta['array_file'])
                if is_extra and not expected_stamp and verify_array((str(root),meta,1536*1024**2))['status']!='verified':raise ValueError('Extra NPZ failed validation')
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
                dest_meta=destination.with_suffix('.json')
                if destination.is_symlink() or dest_meta.is_symlink():raise ValueError('Symlink staged map')
                if expected_stamp and stamp(source)!=expected_stamp:
                    # Creating a hard link updates inode ctime on POSIX. When
                    # resuming, verify the bytes before accepting this change.
                    if not (resume and destination.is_file() and os.path.samefile(source,destination)
                            and file_hash(source)==meta['array_sha256']):
                        raise ValueError('Base NPZ changed; rerun preflight')
                if destination.exists():
                    if not resume:raise ValueError('Duplicate staged array')
                    linked=os.path.samefile(source,destination)
                    if not linked and file_hash(destination)!=meta['array_sha256']:
                        raise ValueError('Staged NPZ changed; use a new assembly directory')
                    links+=int(linked);reused+=1
                elif copy:copy_atomically(source,destination)
                else:
                    try:os.link(source,destination);links+=1
                    except OSError as error:
                        if error.errno!=18:raise
                        copy_atomically(source,destination)
                if dest_meta.exists():
                    if not resume or file_hash(dest_meta)!=file_hash(meta_path):
                        raise ValueError('Staged metadata changed; use a new assembly directory')
                else:copy_atomically(meta_path,dest_meta)
                count+=1;added+=int(is_extra)
                if resume and count%1000==0:
                    write_json(stage/'.wt-assembly-progress.json',{'status':'assembling','maps':count,
                        'new_maps':added,'reused_staged_maps':reused,'elapsed_seconds':time.perf_counter()-started})
            for i,(db,root) in enumerate(zip(databases,roots)):
                for path,raw,meta_hash,array_stamp in db.execute("SELECT path,metadata,meta_hash,array_stamp FROM maps WHERE selected=1 AND status='verified' ORDER BY id"):
                    if not isinstance(meta_hash,str) or len(meta_hash)!=64 or not isinstance(array_stamp,str) or not array_stamp:
                        raise ValueError('Verified index is missing source fingerprints')
                    meta=json.loads(raw);source_meta=safe_file(root,'arrays/'+path)
                    include(root,source_meta,meta,meta_hash,array_stamp,is_extra=i>0)
            for root in sources[len(roots):]:
                for path in sorted((root/'arrays').glob('*.json')):
                    if path.is_symlink():raise ValueError('Symlink extra metadata')
                    include(root,path,read_json(path),is_extra=True)
            if not count:raise ValueError('Empty assembly')
            shutil.copyfile(ammunition_path,stage/'ammunition.json')
            report={'status':'assembled_requires_fresh_preflight','maps':count,'new_maps':added,'physical_source':list(version),
                'hardlinked_arrays':links,'copied_arrays':count-links,'source_files_modified':False,
                'reused_staged_maps':reused,'resumable':resume,
                'duration_seconds':time.perf_counter()-started,
                'shared_bytes_require_hash_invalidation':bool(links),'ammunition_sha256':file_hash(ammunition_path),
                'base_index':str(Path(index_path).resolve()),'source_atlases':[str(p) for p in sources]}
            write_json(stage/'.wt-assembled-atlas',report)
            if resume:
                write_json(stage/'.wt-assembly-progress.json',{'status':'assembled_requires_fresh_preflight',
                    'maps':count,'new_maps':added,'reused_staged_maps':reused,
                    'elapsed_seconds':time.perf_counter()-started})
            os.rename(stage,output)
            return report
    finally:
        for db in databases:db.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('base-index','ammunition','out'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--extra',type=Path,action='append',default=[]);p.add_argument('--copy',action='store_true')
    p.add_argument('--extra-index',type=Path,action='append',default=[],help='An independently verified additional atlas index')
    p.add_argument('--resume',action='store_true',help='Resume a matching incomplete sibling staging directory')
    a=p.parse_args();print(json.dumps(assemble(a.base_index,a.ammunition,a.extra,a.out,copy=a.copy,extra_indexes=a.extra_index,resume=a.resume),indent=2))
if __name__=='__main__':main()
