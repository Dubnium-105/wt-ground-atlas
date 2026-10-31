"""Resolve fixed Datamine aircraft presets and nested containers offline.

Only whole presets declared by the aircraft are marked legal. Unknown dynamic
custom combinations remain mount options; they are never summed into a fake
maximal mixed loadout. This adapter is independent of the public web server.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import copy
import hashlib
import json
from pathlib import Path
from .sources import fetch_bytes


def array(value):
    return value if isinstance(value, list) else ([value] if value is not None else [])


class DatamineLoadouts:
    def __init__(self, config, root):
        self.config, self.root = config, Path(root)
        self.dm = config['datamine']
        self.cache = {}
        self.errors = []

    def resolve_path(self, path):
        if path.startswith('aces.vromfs.bin_u/'):
            return path.lower()
        normalized = path.replace('\\','/').lower()
        if normalized.endswith('.blk'): normalized += 'x'
        return self.dm['resource_prefix'] + normalized

    def read(self, path):
        path = self.resolve_path(path)
        if path not in self.cache:
            raw = fetch_bytes(f"{self.dm['raw_base']}/{self.dm['ref']}/{path}",
                self.root/'data/raw/datamine'/path, self.config['wiki'])
            data = json.loads(raw)
            self.cache[path] = data
        return self.cache[path]

    def weapons(self, weapon, ancestry=()):
        path = weapon.get('blk')
        if not path: raise ValueError('weapon has no resource reference')
        resolved = self.resolve_path(path)
        if resolved in ancestry: raise ValueError('cyclic weapon container')
        data = self.read(path)
        if data.get('container'):
            child = data.get('blk')
            if child:
                count = data.get('bullets')
                if count is None: raise ValueError('container projectile count unknown')
                descendants = self.weapons({'blk':child, 'trigger':weapon.get('trigger')}, ancestry+(resolved,))
                for w in descendants: w['count'] *= count
                return descendants
            nested = data.get('Weapon') or data.get('weapons')
            if nested:
                return [x for w in array(nested) for x in self.weapons(w, ancestry+(resolved,))]
            # Some declared launchers store their projectile directly rather
            # than through another resource. Keep its explicit count below.
            if not isinstance(data.get('rocket') or data.get('bomb') or data.get('bullet'),dict):
                raise ValueError('unsupported container schema')
        projectile = data.get('rocket') or data.get('bomb') or data.get('bullet')
        if not isinstance(projectile, dict):
            raise ValueError('not a supported external projectile')
        trigger = weapon.get('trigger')
        guidance = projectile.get('guidanceType')
        icon = projectile.get('iconType', '')
        if 'arm' in icon or 'radar' in str(guidance).lower() and 'passive' in str(guidance).lower(): typ = 'arm'
        elif trigger == 'aam': typ = 'aam'
        elif data.get('bombGun') and guidance: typ = 'guided_bomb'
        elif data.get('bombGun'): typ = 'bomb'
        elif trigger == 'atgm' or guidance: typ = 'agm'
        else: typ = 'rocket'
        count = weapon.get('bullets', data.get('bullets'))
        # A container's explicit bullets count multiplies this single referenced
        # projectile resource. A top-level launcher still needs a declared count.
        if count is None and ancestry:count=1
        if count is None: raise ValueError('projectile count unknown')
        identifier = projectile.get('bulletName') or Path(resolved).stem
        exact = {'id':identifier, 'name':identifier, 'type':typ, 'count':count,
            'mass_kg':projectile.get('mass'), 'guidance':guidance,
            'explosive_mass_kg':projectile.get('explosiveMass'), 'explosive_type':projectile.get('explosiveType'),
            'range_m':projectile.get('rangeMax'), 'launch_range_m':projectile.get('rangeMax'),
            'max_distance_m':projectile.get('maxDistance'), 'max_overload_g':projectile.get('loadFactorMax'),
            'max_speed_mach':projectile.get('machMax'),
            'requires_emitting_radar':typ=='arm', 'range_semantics':'configured-envelope-not-guaranteed-hit',
            'provenance':{'source_path':resolved, 'commit':self.dm['ref'], 'game_version':self.dm['version']}}
        seeker = projectile.get('guidance', {})
        exact['seeker'] = {k:v for k,v in seeker.items() if 'Seeker' in k or k in ('warmUpTime','acquisitionTime','workTime')}
        exact['flight_envelopes'] = {k:v for k,v in seeker.items() if k.startswith('table')}
        return [exact]

    def enrich(self, vehicle):
        vehicle = copy.deepcopy(vehicle)
        path = self.dm['flightmodels_directory'] + '/' + vehicle['id'] + '.blkx'
        model = self.read(path)
        slots = {}
        for s in array(model.get('WeaponSlots',{}).get('WeaponSlot')):
            slots[s['index']] = {p['name']:p for p in array(s.get('WeaponPreset'))}
        presets = array(model.get('weapon_presets',{}).get('preset'))
        outputs = []
        for preset in presets:
            try:
                data = self.read(preset['blk'])
                declared = array(data.get('Weapon'))
                if not declared:
                    continue
                all_weapons, used_slots = [], set()
                for selection in declared:
                    if 'slot' in selection:
                        si, pn = selection['slot'], selection['preset']
                        if si in used_slots: raise ValueError('duplicate hardpoint in source preset')
                        used_slots.add(si)
                        choice = slots.get(si,{}).get(pn)
                        if choice is None: raise ValueError('source preset option absent from vehicle slots')
                        weapon_list = array(choice.get('Weapon'))
                    elif selection.get('blk'):
                        si = None; pn = None; weapon_list = [selection]
                    else:
                        raise ValueError('unrecognized preset reference')
                    for w in weapon_list:
                        if w.get('trigger') not in self.dm['external_weapon_triggers']:
                            continue
                        leaves = self.weapons(w)
                        for leaf in leaves:
                            leaf['hardpoint'] = si; leaf['mount_preset'] = pn
                        all_weapons.extend(leaves)
                if not all_weapons: continue
                grouped = {}
                for w in all_weapons:
                    key = (w['id'], w['type'])
                    if key not in grouped:
                        grouped[key] = copy.deepcopy(w); grouped[key]['hardpoints'] = [w['hardpoint']]
                    else:
                        grouped[key]['count'] += w['count']; grouped[key]['hardpoints'].append(w['hardpoint'])
                normalized = list(grouped.values())
                outputs.append({'id':preset['name'], 'name':' + '.join(f"{w['count']}×{w['name']}" for w in normalized),
                    'legal':True, 'weapons':normalized, 'mounts':declared,
                    'spawn_cost':None, 'provenance':{'method':'declared-Datamine-preset-with-recursive-container-counts',
                        'commit':self.dm['ref'], 'game_version':self.dm['version'], 'path':self.resolve_path(preset['blk']),
                        'version_compatibility':'pinned-Datamine; Wiki-game-commit-unpublished'}})
            except Exception as exc:
                self.errors.append({'vehicle_id':vehicle['id'], 'preset':preset.get('name'), 'error':str(exc)})
        if outputs:
            vehicle['loadouts'].extend(outputs)
            vehicle['missing'] = [s for s in vehicle['missing'] if s!='aviation.verified_simultaneous_loadout']
        vehicle['datamine_loadout_reference'] = {'commit':self.dm['ref'], 'game_version':self.dm['version'],
            'path':self.resolve_path(path), 'declared_presets':len(presets), 'resolved_presets':len(outputs),
            'model_sha256':hashlib.sha256(json.dumps(model,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        return vehicle


def enrich_catalog(config_path, catalog_path=None, vehicle_ids=None):
    cp=Path(config_path).resolve();root=cp.parent.parent;c=json.loads(cp.read_text())
    catalog_path=Path(catalog_path) if catalog_path else root/'data/snapshots/catalog.json'
    cat=json.loads(catalog_path.read_text());adapter=DatamineLoadouts(c,root)
    targets=[v for v in cat['vehicles'] if v['kind']!='ground' and (vehicle_ids is None or v['id'] in vehicle_ids)]
    enriched={}
    def one(v):
        try:return v['id'],adapter.enrich(v),None
        except Exception as exc:return v['id'],v,str(exc)
    with concurrent.futures.ThreadPoolExecutor(max_workers=c['wiki']['details_workers']) as pool:
        for identifier,vehicle,error in pool.map(one,targets):
            enriched[identifier]=vehicle
            if error:adapter.errors.append({'vehicle_id':identifier,'error':error})
            print(f'Datamine presets {identifier}: {len(vehicle["loadouts"])}',flush=True)
    cat['vehicles']=[enriched.get(v['id'],v) for v in cat['vehicles']]
    cat['snapshot']['coverage']['with_legal_loadout']=sum(any(l['legal'] for l in v['loadouts']) for v in cat['vehicles'])
    cat['snapshot']['coverage']['datamine_aircraft_enriched']=len(enriched)
    catalog_path.write_text(json.dumps(cat,ensure_ascii=False,separators=(',',':')))
    report={'requested':len(targets),'with_presets':sum(bool(v['loadouts']) for v in enriched.values()),
        'config':c['datamine'],'errors':adapter.errors,'note':'No invented custom combinations; only whole source-declared presets are legal.'}
    (root/'reports/datamine-loadout-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',default='config/sources.json');p.add_argument('--catalog');p.add_argument('--vehicles',nargs='*')
    a=p.parse_args();print(json.dumps(enrich_catalog(a.config,a.catalog,a.vehicles),ensure_ascii=False))

if __name__=='__main__':main()
