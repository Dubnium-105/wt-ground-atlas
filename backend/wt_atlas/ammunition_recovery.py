"""Resolve mounted ammunition through exact client localization and selectors.

The native ordered belt remains evidence, not a single maximum-penetration
projectile. Only homogeneous KE selections yield V2 profiles here. Mixed belts
and CE projectiles are retained with explicit reasons for further processing.
"""
from collections import Counter, OrderedDict
import copy
import csv
import gzip
import hashlib
import math
from pathlib import Path
import re

from lxml import html

from .common import content_hash, file_hash, read_json
from .forecast.data import number, safe_file
from .native_binding.ordered_blk import as_json, parse_ordered_blk


def array(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


class Localization:
    def __init__(self, root, *, include_defaults=False):
        self.values = {}; self.sources = {}
        names = ('units_modifications.csv', 'units_weaponry.csv')
        if include_defaults: names += ('menu_options.csv',)
        for name in names:
            path = safe_file(Path(root), name)
            self.sources[name] = file_hash(path)
            with path.open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream, delimiter=';')
                if not reader.fieldnames or '<English>' not in reader.fieldnames:
                    raise ValueError('Missing client English localization column')
                for row in reader:
                    key, value = row[reader.fieldnames[0]], row['<English>']
                    if key in self.values and self.values[key]['value'] != value:
                        raise ValueError('Conflicting exact localization ID: ' + key)
                    self.values[key] = {'key': key, 'value': value, 'file': name,
                                        'sha256': self.sources[name]}

    def match(self, keys, name):
        # Case, punctuation and ID components are deliberately not guessed.
        return [self.values[k] for k in keys if k in self.values and self.values[k]['value'] == name]


class MountedAmmunition:
    def __init__(self, root, localization, *, default_rules=None):
        self.root = Path(root); self.localization = localization
        self.default_rules = default_rules
        self.cache = OrderedDict(); self.sources = {}; self.rejections = []

    def read(self, path):
        key = path.replace('\\', '/').lower()
        source = safe_file(self.root, key)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        sha = file_hash(source)
        if key in self.sources and self.sources[key] != sha:
            raise ValueError('Client source changed during recovery: ' + key)
        data = as_json(parse_ordered_blk(source))
        self.sources[key] = sha; self.cache[key] = data
        if len(self.cache) > 32: self.cache.popitem(last=False)
        return data

    def candidates(self, vehicle):
        path = 'gamedata/units/tankmodels/' + vehicle['id'] + '.blk'
        unit = self.read(path); modifications = unit.get('modifications', {})
        mounts = array(unit.get('commonWeapons', {}).get('Weapon'))
        result = []
        for weapon_path in sorted({m['blk'].replace('\\', '/').lower() for m in mounts if m.get('blk')}):
            weapon = self.read(weapon_path)
            weapon_mounts = [m for m in mounts if m.get('blk', '').replace('\\', '/').lower() == weapon_path]
            selections = [('@default', weapon)] + [(k, v) for k, v in weapon.items()
                          if k in modifications and isinstance(v, dict) and 'bullet' in v]
            for selector, selected in selections:
                bullets = array(selected.get('bullet'))
                if not bullets: continue
                if any(not isinstance(b, dict) or not isinstance(b.get('bulletName'), str) or not b['bulletName'] for b in bullets):
                    self.rejections.append({'unit_path': path, 'weapon_path': weapon_path,
                        'selector': selector, 'reason': 'projectile_identity_missing_or_repeated'})
                    continue
                extra = {}
                if self.default_rules and selector == '@default':
                    from .default_belts import candidate_fields
                    extra = candidate_fields(weapon, weapon_mounts)
                result.append({**extra, 'unit_path': path, 'unit_sha256': self.sources[path],
                    'weapon_path': weapon_path, 'weapon_sha256': self.sources[weapon_path],
                    'selector': selector, 'selection_sha256': content_hash(selected),
                    'bullets': bullets, 'is_belt': weapon.get('isBulletBelt') is True,
                    'mounts': [{k: m[k] for k in ('trigger', 'triggerGroup', 'emitter') if k in m}
                               for m in weapon_mounts]})
        return result

    def bind(self, candidates, ammo):
        matches = []
        for candidate in candidates:
            bullets = candidate['bullets']; selector = candidate['selector']
            keys = [] if selector == '@default' else [selector + '/name']
            # A localized projectile is a selectable name only for a homogeneous
            # selection. One matching member never identifies an entire belt.
            if len({content_hash(b) for b in bullets}) == 1:
                keys += [bullets[0]['bulletName']]
            matched = self.localization.match(keys, ammo['name'])
            extra = {}
            if not matched and self.default_rules:
                from .default_belts import match_default
                matched = match_default(candidate, ammo, self.localization)
                if matched: extra['default_belt_rules'] = self.default_rules
            if matched: matches.append({**candidate, **extra, 'localization': matched})
        if not matches: raise ValueError('exact_localized_mounted_selection_missing')
        # The default and a named selector may contain exactly the same rounds.
        # They are equivalent only within the same mounted weapon source.
        identities = {(m['weapon_path'], content_hash(m['bullets'])) for m in matches}
        if len(identities) != 1: raise ValueError('ambiguous_localized_mounted_selection')
        match = sorted(matches, key=lambda m: (m['selector'] == '@default', m['selector']))[0]
        return {**match, 'equivalent_selectors': sorted({m['selector'] for m in matches})}

    def verify_unchanged(self):
        for path, sha in self.sources.items():
            if file_hash(safe_file(self.root, path)) != sha:
                raise ValueError('Client source changed during recovery: ' + path)


def wiki_tables(raw):
    """Read tables within each weapon section, retaining maximum-only semantics."""
    from .sources import cls, text, scalar
    document = html.fromstring(raw.decode('utf-8'))
    tables = {}
    for weapon in document.xpath(f'//*[{cls("game-unit_weapon")}]'):
        titles = weapon.xpath(f'./*[{cls("game-unit_weapon-title")}]')
        if len(titles) != 1: continue
        for feature in weapon.xpath('.//*[@data-feature-popover]'):
            popover = html.fromstring('<div>' + feature.get('data-feature-popover') + '</div>')
            title = popover.xpath(f'//*[{cls("game-unit_popover-header")}]/span')
            if len(title) != 1: continue
            points = []; maximum = False
            for pen in popover.xpath('//*[@data-bs-title="Armor penetration"]'):
                nested = html.fromstring('<div>' + (pen.get('data-bs-content') or '') + '</div>')
                maximum |= 'Armor penetration (max.)' in text(nested)
                for table in nested.xpath('//table'):
                    rows = table.xpath('.//tr')
                    if not rows: continue
                    headings = rows[0].xpath('./th')
                    ranged = bool(headings and 'Distance' in text(headings[0]))
                    angles = [scalar(text(h)) for h in (headings[1:] if ranged else headings)]
                    for row in rows[1:]:
                        cells = row.xpath('./th|./td')
                        if len(cells) != len(angles) + int(ranged):
                            raise ValueError('Malformed Wiki penetration row')
                        distance = scalar(text(cells[0])) if ranged else None
                        for angle, cell in zip(angles, cells[1:] if ranged else cells):
                            mm = scalar(text(cell))
                            if not number(angle) or not number(mm) or (ranged and not number(distance)):
                                raise ValueError('Malformed Wiki penetration value')
                            points.append({'angle_deg': angle, 'distance_m': distance, 'mm': mm})
            if points:
                key = (text(titles[0]), text(title[0]))
                value = {'points': points, 'maximum_only': maximum}
                if key in tables and tables[key] != value:
                    raise ValueError('Conflicting Wiki penetration tables')
                tables[key] = value
    return tables


def verify_wiki(vehicle, root):
    path = safe_file(Path(root), 'unit_' + vehicle['id'] + '.html.gz')
    raw = gzip.decompress(path.read_bytes()); sha = hashlib.sha256(raw).hexdigest()
    if not any(p.get('sha256') == sha and p.get('url') == vehicle['wiki_url'] for p in vehicle.get('provenance', [])):
        raise ValueError('Wiki source hash or exact vehicle URL mismatch')
    return wiki_tables(raw), {'path': path.name, 'sha256': sha, 'url': vehicle['wiki_url']}


def source_rows(presets, name, prefix, inverse=False):
    preset = presets.get(name)
    if not isinstance(preset, dict): raise ValueError('native_response_preset_missing')
    rows = []
    for table in preset.values():
        if not isinstance(table, dict) or not number(table.get('caliberToArmor')): continue
        points = {}
        for key, values in table.items():
            if not key.lower().startswith(prefix.lower()): continue
            for pair in values if isinstance(values, list) and values and isinstance(values[0], list) else [values]:
                if not isinstance(pair, list) or len(pair) != 2 or not all(number(x) for x in pair):
                    raise ValueError('invalid_native_response_point')
                angle, value = pair
                if not 0 <= angle <= 90 or (inverse and value <= 0) or (not inverse and not 0 <= value <= 1):
                    raise ValueError('invalid_native_response_point')
                x = round(math.sin(math.radians(angle)), 9); y = 1 / value if inverse else value
                if x in points and points[x] != y: raise ValueError('conflicting_native_response_point')
                points[x] = y
        if not points or table['caliberToArmor'] <= 0: raise ValueError('invalid_native_response_table')
        rows.append([table['caliberToArmor'], [[x, y] for x, y in sorted(points.items())]])
    if not rows: raise ValueError('native_response_table_missing')
    rows.sort(key=lambda r: r[0])
    if len({r[0] for r in rows}) != len(rows): raise ValueError('conflicting_native_response_ratio')
    return rows


def ke_profile(ammo, binding, tables, source, response, version, commit):
    bullets = binding['bullets']
    if len({content_hash(b) for b in bullets}) != 1:
        raise ValueError('mixed_belt_requires_individual_projectile_tables')
    bullet = bullets[0]; typ = bullet.get('bulletType', '')
    if not isinstance(typ, str) or not typ.startswith(('ap', 'sap')):
        raise ValueError('projectile_requires_non_ke_model')
    if bullet.get('rocket') or bullet.get('cumulativeDamage'):
        raise ValueError('projectile_requires_non_ke_model')
    if bullet.get('damage', {}).get('kinetic', {}).get('damageType') != 'generic':
        raise ValueError('native_generic_kinetic_mechanism_not_verified')
    caliber = bullet.get('damageCaliber'); assumptions = {}
    if caliber is None and not typ.startswith(('apds', 'apcr')):
        caliber = bullet.get('caliber')
        assumptions['damage_caliber_mm'] = 'Full-bore KE model uses native projectile caliber where damageCaliber is absent; not an explicit damageCaliber source field.'
    if not number(caliber) or caliber <= 0: raise ValueError('native_damage_caliber_missing')
    if not number(ammo.get('caliber_mm')) or not number(bullet.get('caliber')) or not math.isclose(ammo['caliber_mm'], bullet['caliber'] * 1000, abs_tol=.01):
        raise ValueError('catalog_native_caliber_mismatch')
    table_key = (ammo.get('provenance', {}).get('parent_weapon_name'), ammo['name'])
    table = tables.get(table_key)
    if table is None or table['points'] != ammo.get('penetration'):
        raise ValueError('catalog_penetration_differs_from_exact_wiki_table')
    normal = {}
    for point in table['points']:
        if point['angle_deg'] != 0: continue
        distance, mm = point['distance_m'], point['mm']
        if not number(distance) or distance < 0 or not number(mm) or mm <= 0:
            raise ValueError('invalid_ke_range_point')
        if distance in normal and normal[distance] != mm: raise ValueError('conflicting_ke_range_point')
        normal[distance] = mm
    if len(normal) < 2: raise ValueError('ke_range_table_missing')
    curve = sorted(normal.items()); distance, penetration = curve[0]
    if any(b[1] > a[1] for a, b in zip(curve, curve[1:])): raise ValueError('nonmonotonic_ke_range_table')
    slope = source_rows(response['slope'], bullet.get('slopeEffectPreset'), 'slopeEffect', True)
    ricochet = source_rows(response['ricochet'], bullet.get('ricochetPreset'), 'ricochetProbability')
    dispersion = response['dispersion']
    if not number(dispersion) or not 0 <= dispersion < 1: raise ValueError('invalid_native_dispersion')
    evidence = {k: copy.deepcopy(v) for k, v in binding.items() if k != 'bullets'}
    evidence.update(ordered_projectiles=[b['bulletName'] for b in bullets],
        projectile_sha256=content_hash(bullet), wiki=source, wiki_maximum_only=table['maximum_only'],
        maximum_table_usage='homogeneous_projectile_selection_only')
    result = {'mode': 'KE', 'penetration_mm': penetration, 'distance_m': distance,
        'normalized_distance_curve': [[d, mm / penetration] for d, mm in curve],
        'dispersion': dispersion, 'damage_caliber_mm': caliber * 1000, 'mechanism_id': 0,
        'air_loss_mm_per_m': 0., 'slope_rows': slope, 'ricochet_rows': ricochet,
        'provenance': {'client_version': version, 'datamine_commit': commit,
            'penetration_source_ammo_ids': [ammo['id']], 'bullet_name': bullet['bulletName'],
            'bullet_type': typ, 'source_weapon_path': binding['weapon_path'],
            'source_weapon_sha256': binding['weapon_sha256'],
            'source_match_method': 'exact_mounted_selector_and_client_localization',
            'source_hashes': response['hashes'], 'model_assumptions': assumptions,
            'recovery_bindings': {ammo['id']: evidence}}}
    # Identity includes source binding, so distinct source projectiles are never
    # collapsed merely because their approximate physical inputs coincide.
    result['name'] = 'recovered_' + content_hash(result)[:24]
    return result


def consolidate(profiles):
    """Share maps only for identical source projectiles and all physical inputs.

    Every vehicle retains its own mount, selector, localization and Wiki proof.
    Changing any ownership proof changes the complete profile content hash.
    """
    groups = {}; renamed = {}
    for profile in profiles:
        provenance = profile['provenance']
        binding = next(iter(provenance['recovery_bindings'].values()))
        key = content_hash({'physical': {k: v for k, v in profile.items() if k not in ('name', 'provenance')},
            'projectile_sha256': binding['projectile_sha256'],
            **{k: provenance[k] for k in ('client_version', 'datamine_commit', 'source_hashes', 'model_assumptions')}})
        name = 'recovered_' + key[:24]; renamed[profile['name']] = name
        if name not in groups:
            groups[name] = copy.deepcopy(profile); groups[name]['name'] = name
            continue
        target = groups[name]['provenance']
        for ammo_id, evidence in provenance['recovery_bindings'].items():
            if ammo_id in target['recovery_bindings'] and target['recovery_bindings'][ammo_id] != evidence:
                raise ValueError('Conflicting exact recovered ownership proof')
            target['recovery_bindings'][ammo_id] = copy.deepcopy(evidence)
        target['penetration_source_ammo_ids'] = sorted(target['recovery_bindings'])
    return [groups[k] for k in sorted(groups)], renamed


def merge_recovered(existing, additions):
    """Attach new ownership proofs to an existing identical physical profile."""
    result = copy.deepcopy(existing); indices = {p['name']: i for i, p in enumerate(result)}
    for profile in additions:
        if profile['name'] not in indices:
            indices[profile['name']] = len(result); result.append(profile)
            continue
        index = indices[profile['name']]
        merged, _ = consolidate([result[index], profile])
        if len(merged) != 1 or merged[0]['name'] != profile['name']:
            raise ValueError('Recovered profile name conflicts with existing physical identity')
        result[index] = merged[0]
    return result


def verify_existing_recovery(profiles, adapter, localization, wiki_root, version, commit):
    """An incremental import must not preserve stale previously recovered data."""
    checked = set()
    for profile in profiles:
        provenance = profile.get('provenance', {}); bindings = provenance.get('recovery_bindings', {})
        if not bindings: continue
        if (provenance.get('client_version'), provenance.get('datamine_commit')) != (version, commit):
            raise ValueError('Existing recovered profile has a different source version')
        expected = list(provenance.get('source_hashes', {}).items())
        for binding in bindings.values():
            if binding.get('default_belt_rules') and binding['default_belt_rules'] != adapter.default_rules:
                raise ValueError('Existing default-belt GUI evidence changed or was not enabled')
            expected += [(binding['unit_path'], binding['unit_sha256']),
                         (binding['weapon_path'], binding['weapon_sha256'])]
            for loc in binding['localization']:
                if localization.values.get(loc['key']) != loc:
                    raise ValueError('Existing recovered localization changed; recover again from the original bundle')
            wiki = binding['wiki']; key = ('wiki', wiki['path'], wiki['sha256'])
            if key not in checked:
                raw = gzip.decompress(safe_file(Path(wiki_root), wiki['path']).read_bytes())
                if hashlib.sha256(raw).hexdigest() != wiki['sha256']:
                    raise ValueError('Existing recovered Wiki source changed; recover again from the original bundle')
                checked.add(key)
        for path, sha in expected:
            key = ('client', path, sha)
            if key in checked: continue
            adapter.read(path)
            if adapter.sources[path] != sha:
                raise ValueError('Existing recovered client source changed; recover again from the original bundle')
            checked.add(key)


def recover(catalog_path, ammunition_path, capture_path, language_path, wiki_root, rules_root, *, vehicle_ids=None, default_belts=False):
    from .forecast.rule_snapshot import verify_capture
    catalog = read_json(catalog_path); existing = read_json(ammunition_path)
    capture = read_json(capture_path); language = read_json(language_path)
    pack = capture['packs']['aces.vromfs.bin']; version = pack['version']
    snapshot = catalog['snapshot']; commit = snapshot.get('datamine_reference')
    if (language['version'] != version or snapshot.get('datamine_game_version') != version or
        snapshot.get('client_source', {}).get('version') != version or not isinstance(commit, str) or
        not re.fullmatch('[0-9a-f]{40}', commit)):
        raise ValueError('Client, catalog, Datamine and localization must share a version')
    rules_root = Path(rules_root); verify_capture(rules_root, commit)
    if (rules_root / 'version').read_text(encoding='utf-8').strip() != version:
        raise ValueError('Pinned Datamine version differs from the captured client')
    if file_hash(pack['saved']) != pack['sha256'] or file_hash(language['saved']) != language['source_sha256']:
        raise ValueError('Captured source pack checksum mismatch')
    from .default_belts import verified_rules
    default_rules = verified_rules(capture, version) if default_belts else None
    localization = Localization(Path(language['extracted']) / 'lang', include_defaults=default_belts)
    adapter = MountedAmmunition(pack['extracted'], localization, default_rules=default_rules)
    verify_existing_recovery(existing, adapter, localization, wiki_root, version, commit)
    root = 'gamedata/damage_model/'
    response = {'slope': adapter.read(root + 'slope_effect.blk'),
                'ricochet': adapter.read(root + 'ricochet.blk'),
                'dispersion': adapter.read(root + 'damage_system.blk').get('pierceDispersion')}
    response['hashes'] = {root + name: adapter.sources[root + name] for name in
                         ('slope_effect.blk', 'ricochet.blk', 'damage_system.blk')}
    owned = {a for p in existing for a in p.get('provenance', {}).get('penetration_source_ammo_ids', [])}
    profiles = []; records = []; vehicle_errors = []
    for vehicle in catalog['vehicles']:
        if vehicle.get('kind') != 'ground' or (vehicle_ids and vehicle['id'] not in vehicle_ids): continue
        selected = [a for a in vehicle.get('ammo', []) if a['id'] not in owned]
        if not selected: continue
        try:
            candidates = adapter.candidates(vehicle)
            tables, wiki_source = verify_wiki(vehicle, wiki_root)
        except (ValueError, OSError) as error:
            vehicle_errors.append({'vehicle_id': vehicle['id'], 'error': str(error)})
            continue
        for ammo in selected:
            record = {'vehicle_id': vehicle['id'], 'ammo_id': ammo['id']}
            try:
                binding = adapter.bind(candidates, ammo)
                record['binding'] = {k: v for k, v in binding.items() if k != 'bullets'}
                record['ordered_projectiles'] = [{k: b.get(k) for k in
                    ('bulletName', 'bulletType', 'mass', 'caliber', 'speed', 'damageCaliber',
                     'slopeEffectPreset', 'ricochetPreset', 'armorpower', 'cumulativeDamage')}
                    for b in binding['bullets']]
                profile = ke_profile(ammo, binding, tables, wiki_source, response, version, commit)
                profiles.append(profile); record.update(status='profile_recovered', profile=profile['name'])
                # Preserve the Wiki's exact parent-weapon timing for previously
                # untyped belt entries; do not infer a primary gun by position.
                weapon = [w for w in vehicle.get('weapon_profiles', []) if w['id'] == ammo.get('weapon_id')]
                if len(weapon) == 1: ammo['weapon_profile'] = copy.deepcopy(weapon[0])
                ammo['native_ammunition_binding'] = profile['provenance']['recovery_bindings'][ammo['id']]
            except ValueError as error:
                record.update(status='unresolved', reason=str(error))
            records.append(record)
    adapter.verify_unchanged()
    if default_belts and verified_rules(capture, version) != default_rules:
        raise ValueError('Default-belt GUI source changed during recovery')
    for name, sha in localization.sources.items():
        if file_hash(Path(language['extracted']) / 'lang' / name) != sha:
            raise ValueError('Localization changed during recovery')
    recovered_ammunition = len(profiles)
    profiles, renamed = consolidate(profiles)
    combined = merge_recovered(existing, profiles)
    for record in records:
        if record['status'] == 'profile_recovered': record['profile'] = renamed[record['profile']]
    report = {'schema_version': '1.0.0', 'source_version': version, 'datamine_commit': commit,
        'input_catalog_sha256': file_hash(catalog_path), 'input_ammunition_sha256': file_hash(ammunition_path),
        'client_capture_sha256': file_hash(capture_path), 'language_capture_sha256': file_hash(language_path),
        'rules_capture_sha256': file_hash(rules_root / 'capture.json'),
        'default_belt_rules': default_rules,
        'source_files': adapter.sources, 'localization_files': localization.sources,
        'counts': {'examined_ammunition': len(records), 'recovered_profiles': len(profiles),
            'new_profiles': len(combined) - len(existing),
            'recovered_ammunition': recovered_ammunition,
            'recovered_vehicles': len({r['vehicle_id'] for r in records if r['status'] == 'profile_recovered'}),
            'exactly_bound_selections': sum('binding' in r for r in records)},
        'unresolved_reasons': dict(Counter(r['reason'] for r in records if r['status'] == 'unresolved')),
        'records': records, 'vehicle_errors': vehicle_errors, 'source_rejections': adapter.rejections,
        'computed_maps': 0, 'formal_rankings': 0,
        'scope': 'Source-backed homogeneous KE profiles; ordered mixed belts retained without treating maximum penetration as every shot.'}
    return catalog, combined, report
