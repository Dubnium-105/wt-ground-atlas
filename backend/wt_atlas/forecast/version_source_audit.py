"""Compare exact Git objects used by native physics across two source commits.

Git blob equality is useful for identifying invalidated inputs. It does not
prove that an unavailable second-version client GRP or runtime is equivalent.
"""
from collections import Counter
import os
from pathlib import Path
import re
import subprocess

from ..common import content_hash


UNIT_PREFIX='aces.vromfs.bin_u/gamedata/units/tankmodels/'
WEAPON_PREFIX='aces.vromfs.bin_u/gamedata/weapons/groundmodels_weapons/'
GLOBAL_FILES=(
    'aces.vromfs.bin_u/config/damagemodelparts.blkx',
    'aces.vromfs.bin_u/gamedata/damage_model/armor_classes.blkx',
    'aces.vromfs.bin_u/gamedata/damage_model/damage_types.blkx',
    'aces.vromfs.bin_u/gamedata/damage_model/damage_system.blkx',
    'aces.vromfs.bin_u/gamedata/damage_model/ricochet.blkx',
    'aces.vromfs.bin_u/gamedata/damage_model/slope_effect.blkx',
    'char.vromfs.bin_u/config/modifications.blkx',
    'lang.vromfs.bin_u/lang/units_modifications.csv',
    'lang.vromfs.bin_u/lang/units_weaponry.csv',
)


def source_tree(repository,commit):
    """Read tracked blob IDs from a local Git object database without fetching."""
    if not re.fullmatch(r'[0-9a-f]{40}',commit):raise ValueError('Exact Git commit SHA required')
    environment=os.environ.copy();environment['GIT_NO_LAZY_FETCH']='1';environment['GIT_TERMINAL_PROMPT']='0'
    def git(*args):
        return subprocess.run(['git','-C',str(Path(repository).resolve()),*args],check=True,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment).stdout
    if git('cat-file','-t',commit).strip()!=b'commit':raise ValueError('Source object is not a commit')
    root=git('rev-parse',commit+'^{tree}').decode('ascii').strip()
    output=git('ls-tree','-r','-z',commit,'--',UNIT_PREFIX,WEAPON_PREFIX,*GLOBAL_FILES)
    entries={}
    for record in output.split(b'\0'):
        if not record:continue
        header,path=record.split(b'\t',1);mode,kind,sha=header.decode('ascii').split(' ')
        path=path.decode('utf-8')
        if kind!='blob' or mode not in ('100644','100755') or path in entries:
            raise ValueError('Unsupported or ambiguous tracked source object')
        entries[path]={'sha':sha,'mode':mode}
    return {'commit':commit,'root_tree':root,'entries':entries}


def weapon_path(raw):
    prefix='gamedata/weapons/groundmodels_weapons/'
    if not isinstance(raw,str) or not raw.startswith(prefix) or not raw.endswith('.blk'):
        return None
    name=raw[len(prefix):-4]
    if not name or '/' in name or '\\' in name or name in ('.','..'):return None
    return WEAPON_PREFIX+name+'.blkx'


def unit_path(identifier):
    if not isinstance(identifier,str) or not identifier or '/' in identifier or '\\' in identifier or identifier in ('.','..'):
        return None
    return UNIT_PREFIX+identifier+'.blkx'


def compare_path(path,old,new):
    before=old['entries'].get(path);after=new['entries'].get(path)
    return {'path':path,'before':before,'after':after,
        'status':'missing_source' if before is None or after is None else 'unchanged' if before==after else 'changed'}


def audit(old,new,profiles,bindings):
    """Report declared source changes, keeping all physical-version gates closed."""
    global_results=[compare_path(path,old,new) for path in GLOBAL_FILES]
    profile_results=[]
    for profile in profiles:
        provenance=profile.get('provenance',{})
        paths=[weapon_path(provenance.get('source_weapon_path'))]
        for binding in provenance.get('recovery_bindings',{}).values():
            paths.append(weapon_path(binding.get('weapon_path')))
        owner_ids=provenance.get('penetration_source_ammo_ids',[])
        paths.extend(unit_path(x.split('::',1)[0]) if isinstance(x,str) and '::' in x else None for x in owner_ids)
        missing_identity=not owner_ids or any(path is None for path in paths)
        results=[compare_path(path,old,new) for path in sorted(set(x for x in paths if x is not None))]
        statuses={row['status'] for row in results}
        status='source_commit_mismatch' if provenance.get('datamine_commit')!=old['commit'] else 'missing_source_identity' if missing_identity else 'missing_source' if 'missing_source' in statuses else 'changed_source' if 'changed' in statuses else 'unchanged_declared_blobs'
        profile_results.append({'profile':profile['name'],'status':status,
            'changed_or_missing':[r for r in results if r['status']!='unchanged'],
            'compared_paths':len(results)})
    target_results=[]
    for identifier in sorted(bindings):
        path=unit_path(identifier)
        result=compare_path(path,old,new) if path else {'path':None,'status':'missing_source_identity'}
        if bindings[identifier].get('identity',{}).get('datamine_commit')!=old['commit']:
            result['status']='source_commit_mismatch'
        target_results.append({'vehicle_id':identifier,**result})
    old_paths=set(old['entries']);new_paths=set(new['entries'])
    changed=[compare_path(p,old,new) for p in sorted(old_paths|new_paths) if old['entries'].get(p)!=new['entries'].get(p)]
    return {'schema_version':'1.0.0','old_commit':old['commit'],'old_root_tree':old['root_tree'],
        'new_commit':new['commit'],'new_root_tree':new['root_tree'],'changed_paths':changed,
        'global_sources':global_results,'profile_counts':dict(Counter(r['status'] for r in profile_results)),
        'target_counts':dict(Counter(r['status'] for r in target_results)),
        'profiles':profile_results,'targets':target_results,'profiles_sha256':content_hash(profiles),
        'bindings_sha256':content_hash(bindings),'scope':'Git source objects only; no current-client GRP equivalence proof',
        'client_grp_equivalence_verified':False,'promoted_physics_version':False,'publishable':False}
