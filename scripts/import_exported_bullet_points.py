#!/usr/bin/env python3
"""Build isolated V2 research profiles from a supplied client weapons export."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.common import file_hash, read_json, write_json
from wt_atlas.forecast.data import safe_file
from wt_atlas.forecast.exported_bullet_points import research_profiles
from wt_atlas.native_binding.ordered_blk import as_json, parse_ordered_blk

VERSION = '2.59.0.92'
ACES = 'ef7e4861050d4213b2759f3fb817cc78868f777943afc76608642efe25d7b285'
GUI = '1162cd40e5d29a65b7e68e9e2f1c589a4239bf87cf9bde62fa30e14d396b0ccd'
EXPORTER = 'scripts/exportinfo/weaponsinfoexporter.nut'
EXPORTER_LF = '131fe4c3e90c8d215147743e3e1835ae77ce5e66dcb0246c960651305b216fcd'
DATAMINE = 'af0cce561a73704e30c1df66eff06209bcc3f035'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('capture','database','out'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('unit','weapon','selector','projectile'):p.add_argument('--'+name,required=True)
    a=p.parse_args()
    if a.out.resolve() in {a.capture.resolve(),a.database.resolve()}:raise ValueError('Output overlaps source input')
    capture_hash=file_hash(a.capture); database_hash=file_hash(a.database); cap=read_json(a.capture)
    for name,sha in (('aces.vromfs.bin',ACES),('gui.vromfs.bin',GUI)):
        pack=cap['packs'][name]
        if pack['version']!=VERSION or pack['sha256']!=sha or file_hash(pack['saved'])!=sha:
            raise ValueError('Unreviewed source pack: '+name)
    gui=cap['packs']['gui.vromfs.bin']; script=safe_file(Path(gui['extracted']),EXPORTER)
    if hashlib.sha256(script.read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=EXPORTER_LF:
        raise ValueError('Unreviewed exporter script')
    root=Path(cap['packs']['aces.vromfs.bin']['extracted']); source_hashes={}
    def read(path):
        f=safe_file(root,path);source_hashes[path]=file_hash(f);return as_json(parse_ordered_blk(f))
    if not a.unit or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in a.unit):raise ValueError('Invalid unit ID')
    unit=read('gamedata/units/tankmodels/'+a.unit+'.blk')
    mounts=unit['commonWeapons']['Weapon'];mounts=mounts if isinstance(mounts,list) else [mounts]
    weapon=a.weapon.replace('\\','/').lower()
    matches=[m for m in mounts if m.get('blk','').replace('\\','/').lower()==weapon and not m.get('dummy')]
    if len(matches)!=1:raise ValueError('Source weapon must be mounted exactly once')
    if a.selector not in unit.get('modifications',{}):raise ValueError('Selector not directly declared by unit')
    weapon_data=read(weapon);block=weapon_data.get(a.selector,{})
    bullets=block.get('bullet',[]);bullets=bullets if isinstance(bullets,list) else [bullets]
    selected=[x for x in bullets if isinstance(x,dict) and x.get('bulletName')==a.projectile]
    # The exporter collapses identical projectile names. A selector with different
    # raw variants cannot identify which source block supplied the final record.
    if not selected or len({json.dumps(x,sort_keys=True,ensure_ascii=False) for x in selected})!=1:
        raise ValueError('Ambiguous or absent source projectile')
    prefix='gamedata/damage_model/'
    slope=read(prefix+'slope_effect.blk');ricochet=read(prefix+'ricochet.blk');system=read(prefix+'damage_system.blk')
    protected={safe_file(root,path).resolve() for path in source_hashes}
    protected.update((script.resolve(),Path(cap['packs']['aces.vromfs.bin']['saved']).resolve(),
                      Path(gui['saved']).resolve()))
    if a.out.resolve() in protected:raise ValueError('Output overlaps captured source')
    evidence={'capture_sha256':capture_hash,'database_sha256':database_hash,'exporter_lf_sha256':EXPORTER_LF,
        'unit_id':a.unit,'weapon_path':weapon,'selector':a.selector,'projectile':a.projectile,
        'source_version':VERSION,'datamine_commit':DATAMINE,'source_files_sha256':source_hashes,
        'code_sha256_lf':{name:hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
            for name,path in {'importer':Path(__file__),
                'adapter':ROOT/'backend/wt_atlas/forecast/exported_bullet_points.py',
                'response':ROOT/'backend/wt_atlas/ammunition_recovery.py',
                'blk_parser':ROOT/'backend/wt_atlas/native_binding/ordered_blk.py'}.items()}}
    profiles=research_profiles(read_json(a.database),a.unit,selected[0],
        {'slope':slope,'ricochet':ricochet,'dispersion':system.get('pierceDispersion')},evidence)
    if (file_hash(a.capture)!=capture_hash or file_hash(a.database)!=database_hash or
        hashlib.sha256(script.read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=EXPORTER_LF or
        any(file_hash(safe_file(root,path))!=sha for path,sha in source_hashes.items()) or
        any(file_hash(cap['packs'][name]['saved'])!=sha for name,sha in (('aces.vromfs.bin',ACES),('gui.vromfs.bin',GUI)))):
        raise ValueError('Source changed during import')
    write_json(a.out,{'schema_version':'1.0.0','profiles':profiles,'reference_only':True,
        'publishable':False,'formal_rankings':0,'export_version_verified':False,'export_selector_verified':False})
    print(json.dumps({'profiles':len(profiles),'reference_only':True,'publishable':False}))


if __name__=='__main__':main()
