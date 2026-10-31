"""Re-read declared aircraft payloads from a captured client, with source hashes.

This reuses the existing fixed-preset/container parser. Unsupported presets stay
quarantined; a checked hardpoint combination alone never revives old payloads.
"""
import copy
from collections import Counter
from pathlib import Path
from .common import read_json, file_hash, content_hash
from .loadouts import DatamineLoadouts,array
from .native_binding.ordered_blk import parse_ordered_blk, as_json
from .forecast.data import safe_file
from .forecast.mount_rules import audit_vehicle_mounts
from .primary_armament import verified_rules as verify_primary_rules


class CapturedPayloads(DatamineLoadouts):
    def __init__(self, source_root, version, commit, triggers):
        self.source_root=Path(source_root).resolve()
        self.dm={'ref':commit,'version':version,'resource_prefix':'aces.vromfs.bin_u/',
                 'flightmodels_directory':'gamedata/flightmodels','external_weapon_triggers':triggers}
        self.cache={};self.errors=[];self.sources={};self.used=set()

    def resolve_path(self,path):
        path=path.replace('\\','/').lower()
        if path.startswith('aces.vromfs.bin_u/'):path=path[len('aces.vromfs.bin_u/'):]
        if path.endswith('.blkx'):path=path[:-1]
        if not Path(path).suffix:path+='.blk'
        if not path.endswith('.blk'):raise ValueError('Expected a captured BLK source')
        # The shared parser expects Datamine-style paths in provenance.
        safe_file(self.source_root,path)
        return 'aces.vromfs.bin_u/'+path+'x'

    def read(self,path):
        key=self.resolve_path(path);self.used.add(key)
        local=safe_file(self.source_root,key[len('aces.vromfs.bin_u/'):-1])
        if key not in self.cache:
            self.cache[key]=as_json(parse_ordered_blk(local))
            self.sources[key]={'path':local.relative_to(self.source_root).as_posix(),
                               'sha256':file_hash(local),'decoded_sha256':content_hash(self.cache[key])}
        return self.cache[key]

    def weapons(self,weapon,ancestry=()):
        rows=super().weapons(weapon,ancestry)
        for row in rows:
            source=self.sources[row['provenance']['source_path']]
            row['provenance'].update(source_sha256=source['sha256'],decoded_source_sha256=source['decoded_sha256'])
        return rows

    def verify(self,vehicle,shop_unit,source):
        self.used=set();error_start=len(self.errors)
        candidate=copy.deepcopy(vehicle);candidate['loadouts']=[]
        candidate.setdefault('missing',[])
        candidate=self.enrich(candidate)
        audit=audit_vehicle_mounts(candidate,shop_unit,source)
        records=[];verified=[]
        for loadout in candidate['loadouts']:
            # Unsupported source constraints return a vehicle-level audit and
            # deliberately omit per-loadout checks. Keep that rejection explicit.
            check=loadout.get('mount_rule_audit',{'errors':[],
                'source_preset_present':isinstance(shop_unit.get('weapons',{}).get(loadout['id']),dict)})
            errors=list(check['errors'])
            model=self.read('gamedata/flightmodels/'+vehicle['id']+'.blk')
            slots={s['index']:{p['name']:p for p in array(s.get('WeaponPreset'))}
                   for s in array(model.get('WeaponSlots',{}).get('WeaponSlot'))}
            declared=self.read(loadout['provenance']['path'])
            for selection in array(declared.get('Weapon')):
                weapons=array(slots.get(selection['slot'],{}).get(selection.get('preset'),{}).get('Weapon')) if 'slot' in selection else [selection]
                if any(w.get('trigger') not in self.dm['external_weapon_triggers'] for w in weapons):
                    errors.append('unsupported_carried_weapon_trigger')
            if not check['source_preset_present']:errors.append('source_shop_preset_missing')
            if audit['status']=='unsupported_source_constraints':errors.append('unsupported_source_constraints')
            if candidate.get('mount_rule') and not loadout.get('mounts') and check.get('status')!='source_empty_preset_checked':
                errors.append('mount_selection_missing')
            if any(not isinstance(w.get('count'),int) or isinstance(w['count'],bool) or w['count']<=0 for w in loadout['weapons']+candidate['default_primary_armament']['weapons']):
                errors.append('source_weapon_count_invalid')
            row={'loadout_id':loadout['id'],'status':'rejected' if errors else 'verified_client_payload',
                 'errors':sorted(set(errors))}
            records.append(row)
            if errors:continue
            loadout.update(legal=True,source_status='official_verified',source_version=self.dm['version'],
                           source=source['client_source'])
            loadout['provenance'].update(method='captured-client-declared-fixed-preset',
                version_compatibility='captured-client-version-and-pinned-shop-rules',
                client_capture_sha256=source['client_capture_sha256'],
                wpcost_sha256=source['wpcost_sha256'])
            verified.append(loadout)
        # Preserve failed and omitted historical configurations as quarantined
        # records, rather than silently claiming they were all checked.
        ids={l['id'] for l in verified}
        previous=[]
        for old in vehicle.get('loadouts',[]):
            if old['id'] in ids:continue
            old=copy.deepcopy(old);old.update(legal=False,source_status='unvalidated')
            old['refresh_blocker']='not_revalidated_from_captured_client'
            previous.append(old)
        candidate['loadouts']=verified+previous
        candidate['client_payload_sources']={k:self.sources[k] for k in sorted(self.used)}
        for key,item in candidate['client_payload_sources'].items():
            if file_hash(safe_file(self.source_root,item['path']))!=item['sha256']:
                raise ValueError('Captured payload source changed during import: '+key)
        return candidate,{'vehicle_id':vehicle['id'],'verified':len(verified),'quarantined':len(previous),
            'records':records,'parser_errors':self.errors[error_start:],
            'unresolved_mount_relations':audit.get('unresolved_relations',[]),
            'source_files':len(self.used),'source_manifest_sha256':content_hash(candidate['client_payload_sources'])}


def import_payloads(catalog_path,capture_path,rules_root,commit,config_path):
    from .forecast.rule_snapshot import verify_capture,WPCOST_PATH
    capture=read_json(capture_path);packs=capture['packs']
    versions={v['version'] for v in packs.values()}
    if len(versions)!=1:raise ValueError('Mixed client pack versions')
    version=next(iter(versions));rules_root=Path(rules_root)
    verify_capture(rules_root,commit)
    if (rules_root/'version').read_text(encoding='utf-8').strip()!=version:
        raise ValueError('Client/rules version mismatch')
    for name,pack in packs.items():
        if file_hash(pack['saved'])!=pack['sha256']:raise ValueError('Captured pack checksum mismatch: '+name)
    client_wpcost=Path(packs['char.vromfs.bin']['extracted'])/'config/wpcost.blk'
    shop=read_json(rules_root/WPCOST_PATH)
    if as_json(parse_ordered_blk(client_wpcost))!=shop:
        raise ValueError('Pinned rules differ from the captured client')
    config=read_json(config_path)
    primary_rules=verify_primary_rules(capture,version)
    adapter=CapturedPayloads(packs['aces.vromfs.bin']['extracted'],version,commit,config['datamine']['external_weapon_triggers'])
    catalog=read_json(catalog_path);rows=[]
    source={'version':version,'commit':commit,'client_capture_sha256':file_hash(capture_path),
        'primary_armament_rules':primary_rules,
        'client_source':'client://'+packs['aces.vromfs.bin']['sha256']+'/aces.vromfs.bin',
        'wpcost_sha256':file_hash(rules_root/WPCOST_PATH),
        'wpcost_url':'https://raw.githubusercontent.com/gszabi99/War-Thunder-Datamine/'+commit+'/'+WPCOST_PATH}
    for i,vehicle in enumerate(catalog['vehicles']):
        if vehicle.get('kind')=='ground':continue
        try:
            source_id=vehicle.get('rule_snapshot_reference',{}).get('binding',{}).get('source_id')
            if source_id not in shop:raise ValueError('Exact pinned shop identity missing')
            catalog['vehicles'][i],row=adapter.verify(vehicle,shop[source_id],source)
            rows.append(row)
        except (ValueError,KeyError,OSError) as error:
            quarantined=copy.deepcopy(vehicle)
            for loadout in quarantined.get('loadouts',[]):
                loadout.update(legal=False,source_status='unvalidated',refresh_blocker='captured_client_refresh_failed')
            catalog['vehicles'][i]=quarantined
            rows.append({'vehicle_id':vehicle['id'],'verified':0,'quarantined':len(vehicle.get('loadouts',[])),
                         'error':str(error)})
    report={'schema_version':'1.0.0','source':source,'vehicles':rows,
        'counts':{'vehicles':len(rows),'with_verified_payloads':sum(r['verified']>0 for r in rows),
                  'verified_loadouts':sum(r['verified'] for r in rows),'quarantined_loadouts':sum(r['quarantined'] for r in rows),
                  'vehicle_errors':sum('error' in r for r in rows)},
        'parser_errors':dict(Counter(e['error'] for r in rows for e in r.get('parser_errors',[]))),
        'promoted_snapshot_release_status':False,'mission_effects_generated':False,'spawn_costs_generated':False}
    catalog.setdefault('snapshot',{})['client_payload_import']={'source':source,'counts':report['counts']}
    return catalog,report
