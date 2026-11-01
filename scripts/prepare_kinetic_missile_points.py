#!/usr/bin/env python3
"""Recover exact-distance kinetic missile research profiles from captured sources."""
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from wt_atlas.ammunition_recovery import Localization, array, selected_projectiles, verify_wiki
from wt_atlas.common import content_hash, file_hash, read_json, write_json
from wt_atlas.forecast.data import safe_file
from wt_atlas.kinetic_missile_points import profiles
from wt_atlas.native_binding.ordered_blk import as_json, parse_ordered_blk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('catalog', 'recovery', 'gap-summary', 'source-root', 'language-root', 'wiki-root', 'out', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    inputs = {'catalog': args.catalog, 'recovery': args.recovery, 'gap_summary': args.gap_summary}
    if len({args.out.resolve(), args.report.resolve()}) != 2 or {args.out.resolve(), args.report.resolve()} & {p.resolve() for p in inputs.values()}:
        raise ValueError('Keep outputs separate from each other and source inputs')
    hashes = {name: file_hash(path) for name, path in inputs.items()}
    catalog, recovery, summary = (read_json(inputs[name]) for name in ('catalog', 'recovery', 'gap_summary'))
    if (hashes['catalog'] != recovery['output_catalog_sha256'] or
        hashes['catalog'] != summary['catalog_sha256'] or
        hashes['recovery'] != summary['source_recovery_sha256']):
        raise ValueError('Catalog or recovery source checksum mismatch')
    version, commit = recovery['source_version'], recovery['datamine_commit']
    if (catalog['snapshot']['datamine_game_version'] != version or
        catalog['snapshot']['client_source']['version'] != version or catalog['snapshot']['datamine_reference'] != commit):
        raise ValueError('Source version mismatch')
    localization = Localization(args.language_root, include_defaults=True)
    if localization.sources != recovery['localization_files']:
        raise ValueError('Client localization checksum mismatch')
    used = {}
    def read_source(path, expected=None):
        source = safe_file(args.source_root, path)
        actual = file_hash(source)
        if actual != (expected or recovery['source_files'].get(path)):
            raise ValueError('Native source checksum mismatch: ' + path)
        used[path] = actual
        return as_json(parse_ordered_blk(source))
    prefix = 'gamedata/damage_model/'
    slope = read_source(prefix + 'slope_effect.blk')
    ricochet = read_source(prefix + 'ricochet.blk')
    system = read_source(prefix + 'damage_system.blk')
    response = {'slope': slope, 'ricochet': ricochet, 'dispersion': system.get('pierceDispersion'),
                'hashes': dict(used)}
    vehicles = {v['id']: v for v in catalog['vehicles']}
    if len(vehicles) != len(catalog['vehicles']): raise ValueError('Duplicate catalog vehicle')
    result, rejected, wiki_receipts = [], [], []
    records = [r for r in recovery['records'] if r.get('status') == 'unresolved' and
               any(b.get('bulletType') == 'atgm_ke_tank' for b in r.get('ordered_projectiles', []))]
    for record in records:
        try:
            vehicle = vehicles[record['vehicle_id']]
            ammo = [a for a in vehicle.get('ammo', []) if a['id'] == record['ammo_id']]
            if len(ammo) != 1: raise ValueError('Catalog ammunition identity mismatch')
            ammo = ammo[0]; proof = copy.deepcopy(record['binding'])
            if proof.get('unit_path') != 'gamedata/units/tankmodels/' + vehicle['id'] + '.blk':
                raise ValueError('Vehicle source path mismatch')
            unit = read_source(proof['unit_path'], proof['unit_sha256'])
            weapon = read_source(proof['weapon_path'], proof['weapon_sha256'])
            mounts = [m for m in array(unit.get('commonWeapons', {}).get('Weapon'))
                      if m.get('blk', '').replace('\\', '/').lower() == proof['weapon_path']]
            actual = [{k: m[k] for k in ('trigger', 'triggerGroup', 'emitter') if k in m} for m in mounts]
            if not mounts or actual != proof['mounts'] or any(m.get('dummy') or m.get('trigger') == 'countermeasures' for m in mounts):
                raise ValueError('Mounted source weapon mismatch')
            selected = selected_projectiles(weapon, proof['selector'])
            default = selected_projectiles(weapon, '@default')
            if len(selected) != 1 or content_hash(selected) != content_hash(default):
                raise ValueError('Default source projectile differs from selected projectile')
            if content_hash(selected[0]) != proof['projectile_identities'][0]['projectile_sha256']:
                raise ValueError('Exact source projectile mismatch')
            if any(localization.values.get(item['key']) != item for item in proof['localization']):
                raise ValueError('Client localization identity mismatch')
            if weapon.get('isBulletBelt') is not False or proof.get('primary_modification_id'):
                raise ValueError('Unsupported belt or conditional primary weapon')
            tables, wiki = verify_wiki(vehicle, args.wiki_root)
            wiki_receipts.append((vehicle, wiki))
            key = (ammo.get('provenance', {}).get('parent_weapon_name'), ammo['name'])
            result.extend(profiles(ammo, {**proof, 'bullets': selected}, tables.get(key), wiki, response, version, commit))
        except (ValueError, KeyError, TypeError, OSError) as error:
            rejected.append({'ammo_id': record['ammo_id'], 'reason': str(error)})
    if any(file_hash(inputs[name]) != sha for name, sha in hashes.items()) or any(
        file_hash(safe_file(args.source_root, path)) != sha for path, sha in used.items()):
        raise ValueError('Source changed during recovery')
    if any(verify_wiki(vehicle, args.wiki_root)[1] != wiki for vehicle, wiki in wiki_receipts):
        raise ValueError('Wiki source changed during recovery')
    for name, sha in localization.sources.items():
        if file_hash(safe_file(args.language_root, name)) != sha: raise ValueError('Localization changed during recovery')
    if len({p['name'] for p in result}) != len(result): raise ValueError('Duplicate recovered point')
    write_json(args.out, result)
    report = {'schema_version': '1.0.0', 'source_version': version, 'datamine_commit': commit,
              'profiles': len(result), 'source_selections': len(records), 'rejected': rejected,
              'input_sha256': hashes, 'source_files': used, 'localization_files': localization.sources,
              'code_sha256': {'adapter': file_hash(ROOT / 'backend/wt_atlas/kinetic_missile_points.py'),
                              'producer': file_hash(Path(__file__))},
              'output_sha256': file_hash(args.out), 'publishable': False, 'formal_rankings': 0,
              'scope': 'Exact-distance conditional impact research only. Reference profiles are rejected by the ranking engine; no flight, launch eligibility, damage or hit probability is inferred.'}
    write_json(args.report, report)
    print(json.dumps({k: report[k] for k in ('profiles', 'source_selections', 'rejected', 'publishable')}))


if __name__ == '__main__':
    main()
