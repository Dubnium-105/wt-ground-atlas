"""Index the installed ground-unit and weapon source records without guessing aliases.

Weapon files describe projectile options. Unit modifications and presets remain
separate evidence; the index does not assert that every option is selectable.
"""
from pathlib import Path
from collections import Counter
import gzip
from .common import read_json, write_json, file_hash, atomic_write
from .client import client_paths
from .native_binding.ordered_blk import parse_ordered_blk, as_json
from .native_binding.native_material import typed_parameter


def index_client_sources(root, config):
    root=Path(root);work,extracted=client_paths(root,config)
    catalog=read_json(root/'data/snapshots/catalog.json')
    files={p.relative_to(extracted).as_posix().lower():p for p in extracted.rglob('*.blk')}
    source_cache={}; weapons={}; units=[];missing=[]
    def source(path):
        key=path.replace('\\','/').lower()
        if key not in files:
            raise FileNotFoundError(key)
        if key not in source_cache:
            p=files[key];source_cache[key]=(parse_ordered_blk(p),file_hash(p))
        return source_cache[key]
    def mounts(block, prefix):
        out=[]
        for i,child in enumerate(block['children']):
            location=prefix+'/'+child['name']+'['+str(i)+']'
            if child['name']=='Weapon':
                values=as_json(child);path=values.get('blk')
                if path:
                    key=path.replace('\\','/').lower()
                    out.append({'weapon_path':key,'source_block':location,
                                **{k:values[k] for k in ['trigger','triggerGroup','bullets','autoLoader','reloadTime','speedYaw','speedPitch'] if k in values}})
                    if key not in weapons:
                        try:
                            tree,sha=source(key)
                            options=[]
                            def bullets(node, selector):
                                for j,b in enumerate(node['children']):
                                    if b['name']=='bullet':
                                        values=as_json(b)
                                        options.append({'selector':selector,'bullet_index':j,
                                            **{k:values[k] for k in ['bulletName','bulletType','mass','caliber','speed','damageCaliber','armorPower','cumulativeDamage','armorpower','ricochetPreset','slopeEffectPreset'] if k in values}})
                                    else:
                                        bullets(b,selector+'/'+b['name'])
                            bullets(tree,'@weapon')
                            dest=work/'source_weapons'/(sha+'.json')
                            write_json(dest,as_json(tree))
                            weapons[key]={'source_path':key,'sha256':sha,'projectile_options':options,
                                          'unit_availability_requires_preset_and_modification_match':True}
                        except FileNotFoundError:
                            weapons[key]={'source_path':key,'status':'missing_weapon_source'}
                            missing.append({'kind':'weapon','path':key})
            out.extend(mounts(child,location))
        return out
    for vehicle in catalog['vehicles']:
        if vehicle['kind']!='ground':continue
        key='gamedata/units/tankmodels/'+vehicle['id']+'.blk'
        tree,sha=source(key)
        direct=next((c for c in tree['children'] if c['name']=='commonWeapons'),{'children':[]})
        preset_root=next((c for c in tree['children'] if c['name']=='weapon_presets'),{'children':[]})
        modifications=next((c for c in tree['children'] if c['name']=='modifications'),{'children':[]})
        presets=[]
        for preset in preset_root['children']:
            path=typed_parameter(preset['params'],'blk','t')
            if not path:continue
            name=typed_parameter(preset['params'],'name','t')
            try:
                block,digest=source(path)
                presets.append({'name':name,'source_path':path.lower(),'sha256':digest,'mounts':mounts(block,'@preset')})
            except FileNotFoundError:
                presets.append({'name':name,'source_path':path.lower(),'status':'missing_preset_source'})
                missing.append({'kind':'preset','vehicle_id':vehicle['id'],'path':path})
        row={'vehicle_id':vehicle['id'],'model':typed_parameter(tree['params'],'model','t'),
             'source_path':key,'unit_sha256':sha,'common_mounts':mounts(direct,'commonWeapons'),
             'presets':presets,'modifications':[c['name'] for c in modifications['children']]}
        units.append(row)
        write_json(work/'source_units'/(vehicle['id']+'.json'),as_json(tree))
    snapshot={'schema_version':1,'source_version':config['scope']['source_version'],
              'source_vromfs_sha256':read_json(work/'extraction.json')['vromfs_sha256'],
              'units':units,'weapons':list(weapons.values()),'missing':missing,
              'scope':'Source weapon mounts, presets and projectile definitions; not a catalog ammunition alias or full ballistic profile.'}
    write_json(work/'source-index.json',snapshot)
    report={'source_version':snapshot['source_version'],'ground_units':len(units),'weapon_sources':len(weapons),
            'projectile_definitions':sum(len(w.get('projectile_options',[])) for w in weapons.values()),
            'presets':sum(len(u['presets']) for u in units),'missing':missing}
    write_json(root/'reports/client-sources.json',report)
    return report


def link_client_catalog(root,config):
    """Add separately attributed client coverage to each existing vehicle record."""
    root=Path(root);work,_=client_paths(root,config)
    catalog=read_json(root/'data/snapshots/catalog.json')
    index=read_json(work/'source-index.json');units={u['vehicle_id']:u for u in index['units']}
    rows=[]
    for vehicle in catalog['vehicles']:
        if vehicle['kind']!='ground':continue
        unit=units[vehicle['id']];job=read_json(work/'jobs'/(vehicle['id']+'.json'))
        row={'source_version':config['scope']['source_version'],'unit_sha256':unit['unit_sha256'],
             'model':unit['model'],'geometry_sha256':job.get('grp_sha256'),'collision_resource':job.get('collision_resource'),
             'collision_nodes':job.get('nodes'),'configuration_sha256':job.get('profile_sha256'),
             'probability_status':job['status'],'configuration_state':'pristine_authored_pose',
             'weapon_mounts':len(unit['common_mounts']),'presets':len(unit['presets']),
             'projectile_options_are_not_catalog_aliases':True}
        vehicle.setdefault('source_attributes',{})['client']=row
        rows.append({'vehicle_id':vehicle['id'],**row})
    catalog['snapshot']['client_source']={'version':config['scope']['source_version'],
        'vromfs_sha256':index['source_vromfs_sha256'],'ground_units':len(rows),
        'status_counts':dict(Counter(row['probability_status'] for row in rows))}
    write_json(root/'data/snapshots/catalog.json',catalog)
    atomic_write(root/'data/snapshots/catalog.json.gz',gzip.compress((root/'data/snapshots/catalog.json').read_bytes(),mtime=0))
    write_json(root/'data/snapshots/client-ground.json',{'schema_version':1,**catalog['snapshot']['client_source'],'vehicles':rows})
    return catalog['snapshot']['client_source']
