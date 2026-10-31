"""Exact hardpoint constraints from a pinned wpcost custom_presets block.

These constraints establish allowed mount combinations only. They do not prove
the nested weapons' physical properties, flight envelope or mission effects.
"""
from collections import Counter
import re


def _integer(value):
    return isinstance(value,int) and not isinstance(value,bool) and value>0


def declared_empty_preset(loadout):
    return (loadout.get('external_store_state')=='source_declared_empty' and
            loadout.get('mounts')==[] and loadout.get('weapons')==[] and
            bool(re.fullmatch('[0-9a-f]{64}',str(loadout.get('provenance',{}).get('preset_decoded_sha256','')))))


def verified_empty_preset(loadout):
    audit=loadout.get('mount_rule_audit',{})
    return (declared_empty_preset(loadout) and audit.get('status')=='source_empty_preset_checked' and
            audit.get('source_preset_present') is True and loadout.get('source_status')=='official_verified' and
            audit.get('source_version')==loadout.get('source_version') and not audit.get('errors'))


def import_mount_rule(unit,source):
    custom=unit.get('weapons',{}).get('custom_presets')
    if custom is None:return None
    if not isinstance(custom,dict):raise ValueError('Invalid source custom mounts')
    slots=custom.get('slot',[])
    if isinstance(slots,dict):slots=[slots]
    if not isinstance(slots,list):raise ValueError('Invalid source mount slots')
    result={}
    for slot in slots:
        if not isinstance(slot,dict):raise ValueError('Invalid source mount slot')
        index=slot.get('index')
        if not _integer(index) or str(index) in result:raise ValueError('Duplicate or invalid source mount slot')
        presets={}
        for name,entry in slot.items():
            if name=='index':continue
            if not isinstance(entry,dict):raise ValueError('Unsupported source mount definition')
            weapons={};relations={}
            for key,value in entry.items():
                if key in ('DependentWeaponPreset','BannedWeaponPreset'):
                    if not isinstance(value,dict):
                        raise ValueError('Invalid source mount relation')
                    refs=[]
                    for p,indices in sorted(value.items()):
                        # BLK repeated names become a list in Datamine JSON.
                        if not isinstance(indices,list):indices=[indices]
                        if not indices or any(not _integer(s) for s in indices):
                            raise ValueError('Invalid source mount relation')
                        refs.extend({'slot':s,'preset':p} for s in indices)
                    relations['requires' if key=='DependentWeaponPreset' else 'excludes']=refs
                elif _integer(value):weapons[key]=value
                else:raise ValueError('Unsupported source mount weapon/count')
            presets[name]={'weapons':weapons,**relations}
        result[str(index)]=presets
    unresolved=[]
    for slot,presets in result.items():
        for preset,entry in presets.items():
            for relation in ('requires','excludes'):
                for ref in entry.get(relation,[]):
                    if ref['preset'] not in result.get(str(ref['slot']),{}):
                        # A missing required preset makes this choice impossible;
                        # it does not invalidate unrelated choices on the aircraft.
                        # Keep exclusions too: a nonexistent target cannot be
                        # selected through this rule, and no alias is invented.
                        unresolved.append({'slot':int(slot),'preset':preset,'relation':relation,
                                           'target':dict(ref),'reason':'referenced_preset_absent'})
    return {'slots':result,'source':source['wpcost_url'],'source_sha256':source['wpcost_sha256'],
            'source_version':source['version'],'datamine_commit':source['commit'],
            'status':'pinned_source_constraints_only','unresolved_relations':unresolved}


def check_mounts(mounts,rule):
    """Validate the actual carried slots, including cross-slot dependency rules."""
    if not isinstance(mounts,list) or not mounts:
        return {'errors':['mount_selection_missing'],'selected_weapons':{}}
    chosen={};errors=[];weapons=Counter()
    for mount in mounts:
        if not isinstance(mount,dict):
            errors.append('mount_identity_missing');continue
        slot=mount.get('slot');name=mount.get('preset')
        if not _integer(slot) or not isinstance(name,str):
            errors.append('mount_identity_missing');continue
        if slot in chosen:errors.append('duplicate_mount_slot');continue
        entry=rule['slots'].get(str(slot),{}).get(name)
        if entry is None:errors.append('mount_preset_not_in_source');continue
        chosen[slot]=name;weapons.update(entry['weapons'])
    for slot,name in chosen.items():
        entry=rule['slots'][str(slot)][name]
        if any(chosen.get(r['slot'])!=r['preset'] for r in entry.get('requires',[])):
            errors.append('required_mount_preset_missing')
        if any(chosen.get(r['slot'])==r['preset'] for r in entry.get('excludes',[])):
            errors.append('banned_mount_preset_combination')
    return {'errors':sorted(set(errors)),'selected_weapons':dict(sorted(weapons.items()))}


def audit_vehicle_mounts(vehicle,unit,source):
    """Attach enforceable constraints without reviving unverified payloads."""
    try:rule=import_mount_rule(unit,source)
    except ValueError as error:
        # A source shape we cannot interpret remains an explicit data gap.
        vehicle['mount_rule_audit']={'status':'unsupported_source_constraints','reason':str(error)}
        return {'vehicle_id':vehicle['id'],'status':'unsupported_source_constraints','reason':str(error),'loadouts':[]}
    vehicle['mount_rule_audit']={'status':'constraints_imported' if rule is not None else 'no_custom_mount_block',
                               'unresolved_relations':rule['unresolved_relations'] if rule is not None else []}
    if rule is not None:vehicle['mount_rule']=rule
    rows=[]
    for loadout in vehicle.get('loadouts',[]):
        preset=unit.get('weapons',{}).get(loadout['id'])
        row={'loadout_id':loadout['id'],'source_preset_present':isinstance(preset,dict),
             'promoted_legal':False,'errors':[]}
        if isinstance(preset,dict) and declared_empty_preset(loadout):
            row['status']='source_empty_preset_checked'
        elif rule is not None and loadout.get('mounts'):
            row.update(check_mounts(loadout['mounts'],rule))
            row['status']='source_constraints_rejected' if row['errors'] else 'source_constraints_checked'
        else:row['status']='payload_unverified' if isinstance(preset,dict) else 'source_preset_missing'
        loadout['mount_rule_audit']={**row,'source_version':source['version'],
                                    'source_sha256':source['wpcost_sha256']}
        rows.append(row)
    return {'vehicle_id':vehicle['id'],'status':'constraints_imported' if rule is not None else 'no_custom_mount_block',
            'unresolved_relations':rule['unresolved_relations'] if rule is not None else [],'loadouts':rows}
