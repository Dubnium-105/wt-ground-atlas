"""Offline public-source acquisition. Every game value is parsed from source bytes.

The Wiki's encoded BR conversion is source-described and configured, not a guessed
game rule. Aircraft use trb exclusively. Missing fields remain null. Raw captures
are gzip-compressed, SHA-256-addressed in a source manifest and reusable offline.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime as dt
import gzip
import hashlib
import html as html_std
import json
import math
from pathlib import Path
import re
import time
import urllib.request
from typing import Any

from lxml import html


def text(el):
    return re.sub(r"\s+", " ", el.text_content()).strip() if el is not None else ""


def numbers(value):
    return [float(n) for n in re.findall(r"[-+]?\d+(?:\.\d+)?", value.replace(",", "").replace("−", "-"))]


def scalar(value):
    vals = numbers(value)
    return vals[0] if vals else None


def mass_kg(value):
    """Exact SI conversions; these unit definitions are not vehicle tuning."""
    n = scalar(value)
    if n is None:
        return None
    if re.search(r'\bkg\b', value): return n
    if re.search(r'\bg\b', value): return n / 1000
    if re.search(r'\blbs?\b', value): return n * 0.45359237
    if re.search(r'\bt\b', value): return n * 1000
    return None


def cls(name):
    return f'contains(concat(" ",normalize-space(@class)," ")," {name} ")'


def normalized_mode(node):
    """Remove Arcade/basic variants; keep Realistic/reference and the ace endpoint."""
    node = copy.deepcopy(node)
    for el in node.xpath('.//*[@class]'):
        c = el.get('class', '').split()
        if any(x.startswith('show-char-ab') or x.startswith('show-char-rb-mod-basic') for x in c):
            if el.getparent() is not None:
                el.drop_tree()
    return node


def read_capture(path):
    path = Path(path)
    if path.exists():
        return path.read_bytes()
    if path.with_suffix(path.suffix + '.gz').exists():
        return gzip.decompress(path.with_suffix(path.suffix + '.gz').read_bytes())
    return None


def fetch_bytes(url, path, config, *, refresh=False, compress=False, headers=None):
    path = Path(path)
    existing = read_capture(path)
    if existing is not None and not refresh:
        return existing
    last = None
    for attempt in range(config['retries']):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': config['user_agent'], **(headers or {})})
            with urllib.request.urlopen(req, timeout=config['timeout_s']) as response:
                data = response.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            if compress:
                path.with_suffix(path.suffix + '.gz').write_bytes(gzip.compress(data, mtime=0))
            else:
                path.write_bytes(data)
            return data
        except Exception as exc:
            last = exc
            if attempt + 1 < config['retries']:
                time.sleep(config['retry_backoff_s'] * (attempt + 1))
    raise RuntimeError(f'{url}: {last}')


def wiki_rows(source):
    match = re.search(r"window\.WT_UnitList\s*=\s*'((?:\\.|[^'])*)';", source, re.S)
    if not match:
        raise ValueError('Official Wiki WT_UnitList was not present; refusing empty catalog')
    # The JSON payload is inside a single-quoted JS string. Only apostrophe and
    # backslash escapes belonging to that outer string are decoded here.
    return json.loads(match.group(1).replace("\\'", "'"))


def decode_br(index, cfg):
    if index is None:
        return None
    encoding = cfg['br_encoding']
    scale = pow(10, encoding['decimal_places'])
    # JavaScript Math.round convention, copied from the captured official script.
    return (math.floor(index / encoding['steps_per_whole'] * scale + 0.5) / scale
            + encoding['minimum'])


def tree_metadata(source):
    doc = html.fromstring(source)
    result = {}
    groups = {}
    for el in doc.xpath(f'//*[{cls("wt-tree_group")}]'):
        groups[el.get('data-unit-id')] = [v.get('data-unit-id') for v in el.xpath(f'.//*[{cls("wt-tree_item")}]')]
    for el in doc.xpath(f'//*[{cls("wt-tree_item")}]'):
        identifier = el.get('data-unit-id')
        if not identifier:
            continue
        ancestors = list(el.iterancestors())
        group = next((a for a in ancestors if 'wt-tree_group' in a.get('class', '').split()), None)
        cell = next((a for a in ancestors if a.tag == 'td'), None)
        row = cell.getparent() if cell is not None else None
        branch = list(row).index(cell) if row is not None else None
        req = el.get('data-unit-req') or (group.get('data-unit-req') if group is not None else None)
        pred = groups.get(req, [req]) if req else []
        table = next((a for a in ancestors if a.tag == 'table'), None)
        side = table.getparent().get('style', '') if table is not None else ''
        instance = next((a for a in ancestors if 'wt-tree_instance' in a.get('class', '').split()), None)
        research_style = None
        if instance is not None:
            header = instance.xpath(f'./*[{cls("wt-tree_header")}]/*')
            research_style = next((h.get('style') for h in header if text(h)=='Researchable vehicles'), None)
        researchable = side == research_style if side and research_style else None
        result[identifier] = {'branch': str(branch) if branch is not None else None,
            'folder': group.get('data-unit-id') if group is not None else None,
            'folder_members': groups.get(group.get('data-unit-id'), []) if group is not None else [],
            'predecessors': pred, 'predecessor_group': req if req in groups else None,
            'tree_cell': branch, 'tree_side': side, 'researchable': researchable,
            'is_premium_style': 'wt-tree_item--prem' in el.get('class', '')}
    return result


def parse_catalog(source, kind, cfg, source_url):
    tree = tree_metadata(source)
    rows = wiki_rows(source)
    vehicles = []
    for row in rows:
        identifier, name, nation, rank, encoded_br, status = row[:6]
        encoded_br = encoded_br if isinstance(encoded_br, dict) else {}
        wiki_role = row[7][0][0] if row[7] and row[7][0] else None
        role = cfg['role_mapping'].get(wiki_role, wiki_role)
        if kind == 'helicopter':
            role = 'cas'
        br_key = cfg['br_fields'][kind]
        current_br = decode_br(encoded_br.get(br_key), cfg)
        metadata = tree.get(identifier, {})
        vehicle = {'id': identifier, 'name': name, 'nation': nation, 'kind': kind,
            'role': role, 'rank': rank, 'branch': metadata.get('branch'),
            'folder': metadata.get('folder'), 'folder_members': metadata.get('folder_members', []),
            'predecessors': metadata.get('predecessors', []), 'tree': metadata,
            'special': status != cfg['regular_status_code'] or metadata.get('is_premium_style', False) or metadata.get('researchable') is False,
            'acquisition': {'wiki_status_code': status, 'tree_location': metadata.get('tree_side'), 'researchable':metadata.get('researchable')},
            'br': {'current': current_br, 'planned': current_br}, 'br_source_mode': br_key,
            'source_battle_ratings': {k: decode_br(v, cfg) for k, v in encoded_br.items()},
            'wiki_role': wiki_role, 'wiki_url': cfg['base_url'] + '/unit/' + identifier,
            'image_url': cfg['image_base_url'] + identifier + '.png',
            'configuration_id': identifier, 'equivalence_group': None,
            'mobility': {}, 'firepower': {}, 'protection': {}, 'sensors': {}, 'air_defence': {},
            'ammo': [], 'loadouts': [], 'features': [],
            'provenance': [{'url': source_url, 'method': 'official-WT_UnitList-and-tree-HTML',
                'mode': 'ground_realistic', 'br_field': br_key}],
            'missing': []}
        if current_br is None:
            vehicle['missing'].append('br.ground_realistic')
        if identifier not in tree:
            vehicle['missing'].append('tech_tree_position')
        vehicles.append(vehicle)
    return vehicles


def block_lines(doc):
    values = {}
    for el in doc.xpath(f'.//*[{cls("game-unit_chars-line")} or {cls("game-unit_chars-subline")}]'):
        value = el.xpath(f'./*[{cls("game-unit_chars-value")}]')
        if not value:
            continue
        label = text(el[0])
        values.setdefault(label, []).append(text(normalized_mode(value[0])))
    return values


def parse_air_speed(doc):
    """Keep the source altitude beside the Realistic/reference maximum speed."""
    for block in doc.xpath(f'.//*[{cls("game-unit_chars-block")}]'):
        headers = block.xpath(f'./*/*[{cls("game-unit_chars-header")}]')
        if not any(text(header) == 'Max speed' for header in headers):
            continue
        values = block.xpath(f'.//*[{cls("game-unit_chars-value")}]')
        for value in values:
            rendered = text(normalized_mode(value))
            if not re.search(r'\bkm/h\b', rendered):
                continue
            speed = scalar(rendered)
            if speed is None:
                continue
            row = value.getparent()
            label = ' '.join(text(child) for child in row if child is not value)
            altitude = re.search(r'\bat\s+([\d,]+(?:\.\d+)?)\s*m\b', label)
            return {'forward_kmh': speed,
                'speed_altitude_m': scalar(altitude.group(1)) if altitude else None,
                'speed_semantics': 'source-maximum-at-stated-altitude',
                'speed_source_label': label}
    return {}


def parse_multi_vehicle_system(doc, vehicle):
    """Record explicit Wiki system membership without combining capabilities."""
    nodes = doc.xpath(f'.//*[{cls("game-unit_multiunit-item")}]')
    if not nodes:
        return
    members = []
    for node in nodes:
        href = node.get('href', '')
        linked = re.search(r'/unit/([^/?#]+)', href)
        identifier = linked.group(1) if linked else (
            vehicle['id'] if 'active' in node.get('class', '').split() else None)
        labels = node.xpath(f'.//*[{cls("subtitle")}]')
        names = node.xpath(f'.//*[{cls("name")}]')
        label = text(labels[0]) if labels else ''
        basic = label.lower() == 'basic unit'
        count = re.fullmatch(r'(\d+)\s+vehicles?', label, re.I)
        if identifier:
            members.append({'vehicle_id': identifier, 'name': text(names[0]) if names else None,
                'role': 'basic' if basic else 'component',
                'count': 1 if basic else int(count.group(1)) if count else None,
                'source_label': label})
    basics = [member['vehicle_id'] for member in members if member['role'] == 'basic']
    basic_id = basics[0] if len(basics) == 1 else None
    vehicle['system_id'] = basic_id
    vehicle['system_role'] = next((member['role'] for member in members
        if member['vehicle_id'] == vehicle['id']), None)
    vehicle['multi_vehicle_system'] = {'id': basic_id, 'basic_vehicle_id': basic_id,
        'members': members, 'capabilities_aggregated': False,
        'provenance': {'url': vehicle['wiki_url'], 'method': 'official-Wiki-multiunit-membership'}}


def parse_popover_ammo(popover, vehicle_id):
    d = html.fromstring('<div>' + popover + '</div>')
    penetration = d.xpath('//*[@data-bs-title="Armor penetration"]')
    if not penetration:
        return None
    header = d.xpath(f'//*[{cls("game-unit_popover-header")}]/span')
    if not header:
        return None
    name = text(header[0])
    lines = block_lines(d)
    caliber = scalar(next(iter(lines.get('Caliber', [])), ''))
    # Belt entries named Default/Universal are retained in armament records, but
    # cannot be made into a uniquely identified main projectile without caliber.
    if caliber is None:
        return None
    desc = d.xpath(f'//*[{cls("game-unit_popover-content")}]//div[@class="mb-1"]')
    description = text(desc[0]) if desc else ''
    ammo_type = None
    icon_paths = d.xpath('//img/@src')
    for src in icon_paths:
        leaf = src.rsplit('/', 1)[-1].removesuffix('.png')
        if leaf.endswith('_tank'):
            ammo_type = leaf.removesuffix('_tank').upper()
    if ammo_type == 'APDSFS':
        ammo_type = 'APFSDS'
    points = []
    range_invariant = False
    for pen in penetration:
        nested = pen.get('data-bs-content')
        if not nested:
            continue
        pd = html.fromstring('<div>' + nested + '</div>')
        table = pd.xpath('//table')
        if not table:
            continue
        trs = table[0].xpath('.//tr')
        headers = trs[0].xpath('./th')
        has_distance = bool(headers and 'Distance' in text(headers[0]))
        range_invariant = not has_distance
        angles = [scalar(text(th)) for th in (headers[1:] if has_distance else headers)]
        for tr in trs[1:]:
            ds = tr.xpath('./th|./td')
            if len(ds) != len(angles) + (1 if has_distance else 0):
                continue
            distance = scalar(text(ds[0])) if has_distance else None
            for angle, cell in zip(angles, ds[1:] if has_distance else ds):
                mm = scalar(text(cell))
                if None not in (angle, mm) and (distance is not None or not has_distance):
                    points.append({'distance_m': distance, 'angle_deg': angle, 'mm': mm})
    return {'id': f'{vehicle_id}::{name}', 'name': name, 'type': ammo_type,
        'caliber_mm': caliber, 'mass_kg': mass_kg(next(iter(lines.get('Projectile Mass', [])), '')),
        'muzzle_velocity_m_s': scalar(next(iter(lines.get('Muzzle Velocity', [])), '')),
        'launch_range_m': (scalar(next(iter(lines.get('Launch range', [])), '')) *
            (1000 if 'km' in next(iter(lines.get('Launch range', [])), '') else 1)) if lines.get('Launch range') else None,
        'guidance': next(iter(lines.get('Guidance', [])), None),
        'max_overload_g': scalar(next(iter(lines.get('Maximum overload', [])), '')),
        'missile_guidance_time_s': scalar(next(iter(lines.get('Missile guidance time', [])), '')),
        'penetration': points, 'range_invariant': range_invariant,
        'penetration_distance_semantics': 'source-angle-only-table' if range_invariant else 'source-distance-and-angle-table',
        'description': description,
        'provenance': {'method': 'official-Wiki-exact-ammunition-popover',
            'availability': 'listed-for-this-vehicle', 'canonical_datamine_ammo_id': None}}


def parse_air_weapon(popover):
    d = html.fromstring('<div>' + popover + '</div>')
    h = d.xpath(f'//*[{cls("game-unit_popover-header")}]')
    if not h:
        return None
    title_span = h[0].xpath('./span')
    name = text(title_span[0]) if title_span else text(h[0])
    links = d.xpath('//a[contains(@href,"/collections/weapon/")]/@href')
    if not links:
        return None
    lines = block_lines(d)
    one = lambda k: next(iter(lines.get(k, [])), '')
    distance = lambda s: scalar(s) * (1000 if 'km' in s else 1) if scalar(s) is not None else None
    low = name.lower()
    if 'anti-radiation' in low: typ = 'arm'
    elif 'air-to-air' in low: typ = 'aam'
    elif 'air-to-ground' in low or 'anti-tank' in low: typ = 'agm'
    elif 'guided bomb' in low or ('gbu' in low and one('Guidance')): typ = 'guided_bomb'
    elif 'bomb' in low: typ = 'bomb'
    elif 'rocket' in low: typ = 'rocket'
    elif 'cannon' in low or 'gun' in low: typ = 'gun'
    else: typ = None
    return {'id': links[0].rsplit('/',1)[-1], 'name': name, 'type': typ,
        'guidance': one('Guidance') or None, 'aspect': one('Aspect') or None,
        'mass_kg': mass_kg(one('Projectile Mass')), 'max_overload_g': scalar(one('Maximum overload')),
        'launch_range_m': distance(one('Launch range')), 'range_m': distance(one('Launch range')),
        'range_semantics': 'published-launch-envelope-not-guaranteed-hit',
        'lock_range_rear_m': distance(one('Lock range in rear-aspect')),
        'missile_guidance_time_s': scalar(one('Missile guidance time')),
        'explosive_tnt_kg': mass_kg(one('TNT equivalent')),
        'requires_emitting_radar': typ == 'arm', 'source_attributes': lines,
        'weapon_url': 'https://wiki.warthunder.com' + links[0]}


def parse_air_loadouts(d, vehicle):
    section = next((x.getparent() for x in d.xpath(f'//*[{cls("block-header")}]')
                    if text(x) == 'Suspended armament'), None)
    if section is None:
        return
    loadouts = []
    # Each Setup is an actual simultaneously equipped official preset. They are
    # separate alternatives; the counts are never summed across Setup rows.
    for line in section.xpath(f'.//*[{cls("game-unit_chars-line")}]'):
        h = line.xpath(f'./*[{cls("game-unit_chars-header")}]')
        if not h or not text(h[0]).startswith('Setup '):
            continue
        val = line.xpath(f'./*[{cls("game-unit_chars-value")}]')
        if not val:
            continue
        weapons = []
        for feature in val[0].xpath('.//*[@data-feature-popover]'):
            weapon = parse_air_weapon(feature.get('data-feature-popover'))
            if weapon is None:
                continue
            preceding = feature.getprevious()
            prefix = preceding.tail if preceding is not None else feature.getparent().text
            count = re.search(r'(\d+)\s*[×x]\s*$', prefix or '')
            weapon['count'] = int(count.group(1)) if count else 1
            weapons.append(weapon)
        if weapons:
            loadouts.append({'id': vehicle['id'] + '::' + text(h[0]).replace(' ', '_'),
                'name': text(h[0]) + ' · ' + ' + '.join(f"{w['count']}×{w['name']}" for w in weapons),
                'legal': True, 'weapons': weapons, 'spawn_cost': None,
                'provenance': {'url': vehicle['wiki_url'], 'method': 'official-simultaneous-preset',
                    'scope': 'one-whole-preset-not-summed-mutually-exclusive-presets'}})
    vehicle['loadouts'] = loadouts
    options = []
    for row in section.xpath(f'.//table[{cls("game-unit_slot-list")}]//tbody/tr'):
        cells = row.xpath('./td')
        if len(cells) < 3:
            continue
        feature = cells[0].xpath('.//*[@data-feature-popover]')
        if not feature:
            continue
        weapon = parse_air_weapon(feature[0].get('data-feature-popover'))
        if weapon is None:
            continue
        count = re.match(r'(\d+)\s*[×x]', text(cells[0]))
        weapon['count_per_mount'] = int(count.group(1)) if count else 1
        weapon['mounted_mass_kg'] = scalar(text(cells[1]))
        weapon['allowed_slots'] = [i for i, cell in enumerate(cells[2:]) if cell.xpath('.//img')]
        weapon['legality'] = 'mount-compatible-only; requires-preset-or-dependency-validation'
        options.append(weapon)
    if options:
        vehicle['hardpoint_options'] = options
        attrs = block_lines(section)
        vehicle['payload_limits'] = {'total_mass_kg': scalar(next(iter(attrs.get('Max weight',[])),'')),
            'wing_mass_kg': scalar(next(iter(attrs.get('Maximum',[])),'')),
            'max_imbalance_kg': scalar(next(iter(attrs.get('Max. difference',[])),''))}


def parse_weapon_profiles(d, vehicle):
    profiles = []
    for index, w in enumerate(d.xpath(f'//*[{cls("game-unit_weapon")}]')):
        title = w.xpath(f'./*[{cls("game-unit_weapon-title")}]')
        if not title:
            continue
        name = text(title[0]); attrs = block_lines(w)
        one = lambda k: next(iter(attrs.get(k, [])), '')
        rv = numbers(one('Reload'))
        rb = next((b for b in w.xpath(f'.//*[{cls("game-unit_chars-block")}]') if 'Reload' in text(b)), None)
        if rb is not None:
            sv = rb.xpath(f'.//*[{cls("game-unit_chars-value")}]')
            if sv: rv = numbers(text(normalized_mode(sv[0])))
        rate = scalar(one('Fire rate'))
        vg, hs, vs = numbers(one('Vertical guidance')), numbers(one('Horizontal')), numbers(one('Vertical'))
        kind = 'missile_launcher' if any(k in name.lower() for k in ('missile', 'aam', 'mim-')) else ('automatic' if 'automatic' in name.lower() or 'machine gun' in name.lower() else 'cannon')
        loading = 'autoloader' if 'Autoloader' in text(w) or (not profiles and vehicle['firepower'].get('reload_type') == 'autoloader') else ('manual' if rb is not None and 'aces' in text(rb) and kind == 'cannon' else ('automatic' if kind == 'automatic' else 'mechanism_unspecified'))
        profile = {'id': f"{vehicle['id']}::weapon:{index}", 'name': name, 'kind': kind,
            'reload_type': loading, 'listed_reload_s': rv[-1] if rv else None,
            'reload_ace_s': rv[-1] if rv and loading == 'manual' else None,
            'autoload_s': rv[-1] if rv and loading == 'autoloader' else None,
            'fire_rate_rpm': rate, 'total_ammunition': scalar(one('Ammunition')),
            'min_elevation_deg': vg[0] if vg else None,
            'depression_deg': -vg[0] if vg else None, 'elevation_deg': vg[1] if len(vg)>1 else None,
            'traverse_deg_s': hs[-1] if hs else None, 'vertical_deg_s': vs[-1] if vs else None,
            'first_order_rounds': scalar(one('First-order')), 'belt_capacity': scalar(one('Belt capacity')),
            'ready_rounds': None, 'ready_rounds_note': 'total ammunition is not a verified ready rack',
            'raw': attrs}
        profiles.append(profile)
        for a in vehicle['ammo']:
            # Availability is established by a popover within this exact weapon.
            for element in w.xpath('.//*[@data-feature-popover]'):
                pd = html.fromstring('<div>'+element.get('data-feature-popover')+'</div>')
                ah = pd.xpath(f'//*[{cls("game-unit_popover-header")}]')
                if ah and text(ah[0]) == a['name']:
                    a['weapon_id'] = profile['id']; a['weapon_profile'] = profile
                    break
    vehicle['weapon_profiles'] = profiles
    if profiles:
        first = profiles[0]
        vehicle['firepower'].update({k:first[k] for k in ('reload_type','reload_ace_s','autoload_s','listed_reload_s','min_elevation_deg','depression_deg','elevation_deg','traverse_deg_s','vertical_deg_s','first_order_rounds')})
        vehicle['firepower']['primary_weapon_id'] = first['id']
        vehicle['firepower']['reload_semantics'] = 'source-weapon-specific; missile-launch interval is not human loading'
    vehicle['auxiliary']['machine_guns'] = [{'name': p['name'], 'caliber_mm': (float(re.search(r'(\d+(?:\.\d+)?)\s*mm',p['name']).group(1)) if re.search(r'(\d+(?:\.\d+)?)\s*mm',p['name']) else None),
        'coaxial': 'coaxial' in p['name'].lower()} for p in profiles if 'machine gun' in p['name'].lower()]


def parse_vehicle_details(source, vehicle):
    d = html.fromstring(source)
    lines = block_lines(d)
    first = lambda key: next(iter(lines.get(key, [])), '')
    firstnum = lambda key: scalar(first(key))
    vehicle['mobility'] = {'forward_kmh': firstnum('Forward'), 'reverse_kmh': firstnum('Backward'),
        'power_hp': firstnum('Engine power'), 'mass_t': firstnum('Weight'),
        'power_to_weight_hp_t': firstnum('Power-to-weight ratio'), 'neutral_steering': None,
        'state': 'reference', 'mode': 'realistic'}
    if vehicle['kind'] != 'ground':
        vehicle['mobility']['forward_kmh'] = firstnum('Max speed')
        vehicle['mobility'].update(parse_air_speed(d))
        vehicle['mobility']['turn_time_s'] = firstnum('Turn time')
        vehicle['mobility']['climb_m_s'] = firstnum('Rate of Climb')
    reload_vals = numbers(first('Reload'))
    reload_blocks = [b for b in d.xpath(f'//*[{cls("game-unit_chars-block")}]')
                     if b.xpath(f'./*/*[{cls("game-unit_chars-header")} and text()="Reload"]')]
    rb_text = text(reload_blocks[0]) if reload_blocks else ''
    is_manual = 'basic crew' in rb_text or 'aces' in rb_text
    # Reload is often a separate subline with an empty label.
    if reload_blocks:
        sub = reload_blocks[0].xpath(f'.//*[{cls("game-unit_chars-value")}]')
        if sub:
            reload_vals = numbers(text(normalized_mode(sub[0])))
    vertical = numbers(first('Vertical guidance'))
    hspeed = numbers(first('Horizontal'))
    vspeed = numbers(first('Vertical'))
    vehicle['firepower'] = {'reload_type': ('manual' if is_manual else 'mechanism_unspecified') if reload_vals else None,
        'reload_ace_s': reload_vals[-1] if reload_vals and is_manual else None,
        'autoload_s': None, 'listed_reload_s': reload_vals[-1] if reload_vals else None,
        'min_elevation_deg': vertical[0] if vertical else None,
        'depression_deg': -vertical[0] if vertical else None,
        'elevation_deg': vertical[1] if len(vertical) > 1 else None,
        'traverse_deg_s': hspeed[-1] if hspeed else None,
        'vertical_deg_s': vspeed[-1] if vspeed else None,
        'first_order_rounds': firstnum('First-order'), 'crew_state': 'aces',
        'modification_state': 'reference'}
    hull = numbers(first('Hull'))
    turret = numbers(first('Turret'))
    vehicle['protection'] = {'front_mm': hull[0] if hull else None,
        'side_mm': hull[1] if len(hull) > 1 else None,
        'rear_mm': hull[2] if len(hull) > 2 else None,
        'turret_front_mm': turret[0] if turret else None,
        'turret_side_mm': turret[1] if len(turret) > 1 else None,
        'physical_thickness_only': True, 'rhae_ke_mm': None, 'rhae_ce_mm': None,
        'era': None, 'spall_liner': None, 'crew_count': firstnum('Crew')}
    feature_names = []
    ammo = {}
    thermal_generations = {}
    sensors = {'thermal_generation': None, 'commander_thermal_generation': None,
        'night_vision': None, 'laser_rangefinder': None, 'stabilizer': None}
    for feature in d.xpath('//*[@data-feature-popover]'):
        raw = feature.get('data-feature-popover')
        pd = html.fromstring('<div>' + raw + '</div>')
        title = pd.xpath(f'//*[{cls("game-unit_popover-header")}]')
        title = text(title[0]) if title else ''
        if title and title not in feature_names:
            feature_names.append(title)
        full = text(pd)
        if 'Autoloader' in title:
            vehicle['firepower']['reload_type'] = 'autoloader'
            vehicle['firepower']['autoload_s'] = reload_vals[-1] if reload_vals else None
            vehicle['firepower']['reload_ace_s'] = None
        if title == 'ERA' or 'explosive reactive' in title.lower():
            vehicle['protection']['era'] = True
        if 'spall' in title.lower():
            vehicle['protection']['spall_liner'] = True
        if 'neutral steering' in title.lower():
            vehicle['mobility']['neutral_steering'] = True
        if 'Night vision' in title:
            sensors['night_vision'] = True
        if 'Laser rangefinder' in title:
            sensors['laser_rangefinder'] = True
        if 'Radar/Optical Detection' in title:
            sensors['radar'] = True
            sensors['radar_source_attributes'] = block_lines(pd)
        if 'stabilizer' in title.lower():
            sensors['stabilizer'] = title
        for seat, kind, gen in re.findall(r'(Gunner|Commander|Driver):\s*(TVD|NVD),\s*Generation\s*(\d+)', full):
            thermal_generations[seat.lower()] = {'type': kind, 'generation': int(gen)}
        parsed = parse_popover_ammo(raw, vehicle['id'])
        if parsed:
            parsed['provenance']['url'] = vehicle['wiki_url']
            ammo[parsed['id']] = parsed
    if thermal_generations.get('gunner', {}).get('type') == 'TVD':
        sensors['thermal_generation'] = thermal_generations['gunner']['generation']
    if thermal_generations.get('commander', {}).get('type') == 'TVD':
        sensors['commander_thermal_generation'] = thermal_generations['commander']['generation']
    sensors['seat_optics'] = thermal_generations
    vehicle['sensors'] = sensors
    vehicle['ammo'] = list(ammo.values())
    vehicle['features'] = feature_names
    vehicle['auxiliary'] = {'smoke_grenades': True if 'Smoke grenades' in feature_names else None,
        'engine_smoke': True if any('engine smoke' in f.lower() for f in feature_names) else None}
    parse_weapon_profiles(d, vehicle)
    parse_multi_vehicle_system(d, vehicle)
    if vehicle['kind'] != 'ground':
        parse_air_loadouts(d, vehicle)
    if vehicle['role'] == 'spaa':
        missile_ranges = [a['launch_range_m'] for a in vehicle['ammo'] if a.get('launch_range_m') is not None]
        missile_g = [a['max_overload_g'] for a in vehicle['ammo'] if a.get('max_overload_g') is not None]
        vehicle['air_defence'] = {'missile_range_m': max(missile_ranges) if missile_ranges else None,
            'missile_overload_g': max(missile_g) if missile_g else None, 'ready_missiles': None,
            'radar_scan_deg': None, 'radar_scan_period_s': None,
            'radar': sensors.get('radar'), 'weapon_profiles': vehicle['weapon_profiles'],
            'range_semantics': 'source-maximum-launch-range-not-guaranteed-engagement'}
    vehicle['source_attributes'] = {k: v for k, v in lines.items() if k and k not in {'AB', 'RB', 'SB', 'Aces', 'Experts'}}
    vehicle['provenance'].append({'url': vehicle['wiki_url'],
        'method': 'official-Wiki-HTML-characteristics-reference-realistic-aces',
        'sha256': hashlib.sha256(source.encode()).hexdigest(), 'precision': 'source-stat-card'})
    missing = vehicle['missing']
    if not ammo and vehicle['kind'] == 'ground': missing.append('firepower.exact_ammunition')
    if vehicle['kind'] != 'ground' and not vehicle['loadouts']: missing.append('aviation.verified_simultaneous_loadout')
    if vehicle['role'] == 'spaa': missing.append('air_defence.radar_and_missile_envelope')
    missing.extend(['protection.calibrated_rhae', 'protection.precise_directional_probability'])
    if vehicle['firepower']['reload_type'] == 'mechanism_unspecified': missing.append('firepower.loading_mechanism')
    return vehicle


def build_catalog(config, root, *, refresh=False, details=True, limit=None):
    root = Path(root)
    cfg = config['wiki']
    cache = root / config['cache']['raw_directory']
    vehicles = []
    captures = []
    for slug, kind in cfg['catalogs'].items():
        url = cfg['base_url'] + '/' + slug
        raw = fetch_bytes(url, cache / (slug + '.html'), cfg, refresh=refresh)
        data = parse_catalog(raw.decode(), kind, cfg, url)
        vehicles.extend(data)
        captures.append({'url': url, 'sha256': hashlib.sha256(raw).hexdigest(), 'count': len(data), 'kind': kind})
    errors = []
    detail_count = 0
    if details:
        wanted = vehicles[:limit] if limit is not None else vehicles
        def enrich(v):
            try:
                raw = fetch_bytes(v['wiki_url'], cache / ('unit_' + v['id'] + '.html'), cfg,
                                  refresh=refresh, compress=config['cache']['compress_details'])
                parse_vehicle_details(raw.decode(), v)
                return True, v['id']
            except Exception as exc:
                v['missing'].append('wiki.detail_unavailable')
                return False, {'vehicle_id': v['id'], 'error': str(exc)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=cfg['details_workers']) as pool:
            for n, (ok, detail) in enumerate(pool.map(enrich, wanted), 1):
                if ok: detail_count += 1
                else: errors.append(detail)
                if n % 100 == 0 or n == len(wanted):
                    print(f'Wiki details {n}/{len(wanted)}; success={detail_count}; failures={len(errors)}', flush=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    known_nations = list(dict.fromkeys(v['nation'] for v in vehicles))
    nations = [{'id': n, 'label': cfg['nation_labels'].get(n, n)} for n in cfg['nation_labels'] if n in known_nations]
    nations.extend({'id': n, 'label': n} for n in known_nations if n not in cfg['nation_labels'])
    digest = hashlib.sha256(''.join(c['sha256'] for c in captures).encode()).hexdigest()
    catalog = {'schema_version': config['schema_version'], 'snapshot': {
        'id': 'wiki-' + digest[:16], 'collected_at': now, 'source_url': cfg['base_url'],
        'game_version': None, 'commit': None, 'datamine_reference': config['datamine']['ref'],
        'datamine_game_version': config['datamine']['version'],
        'version_note': 'Wiki does not expose its game commit; catalog capture and pinned Datamine version are separate provenance, not asserted identical.',
        'coverage': {'catalog_vehicles': len(vehicles), 'details_parsed': detail_count,
            'kinds': {k: sum(v['kind'] == k for v in vehicles) for k in cfg['catalogs'].values()},
            'ground_rb_known': sum(v['br']['current'] is not None for v in vehicles),
            'with_ammunition': sum(bool(v['ammo']) for v in vehicles),
            'with_legal_loadout': sum(bool(v['loadouts']) for v in vehicles),
            'with_custom_hardpoint_options':sum(bool(v.get('hardpoint_options')) for v in vehicles),
            'legal_whole_presets':sum(len(v['loadouts']) for v in vehicles),
            'with_ace_reload':sum(v['firepower'].get('reload_ace_s') is not None for v in vehicles),
            'with_auto_reload':sum(v['firepower'].get('autoload_s') is not None for v in vehicles),
            'with_thermal_generation':sum(v['sensors'].get('thermal_generation') is not None for v in vehicles),
            'with_sam_range':sum(v['air_defence'].get('missile_range_m') is not None for v in vehicles),
            'with_air_max_speed':sum(v['kind'] != 'ground' and v['mobility'].get('forward_kmh') is not None for v in vehicles),
            'multi_vehicle_system_records':sum(v.get('system_id') is not None for v in vehicles),
            'multi_vehicle_systems':len({v['system_id'] for v in vehicles if v.get('system_id') is not None}),
            'special_vehicles':sum(v['special'] for v in vehicles)},
        'captures': captures}, 'nations': nations, 'vehicles': vehicles}
    return catalog, {'schema_version': config['schema_version'], 'collected_at': now,
        'captures': captures, 'errors': errors, 'unresolved': {
            'wiki_game_version': 'unknown; no false matching to client geometry version',
            'equivalent_reskins': 'unverified; no inferred equivalence from family names',
            'ground_rb_mode': 'aircraft trb, ground and helicopter rb from official directory',
            'multi_vehicle_systems': 'explicit source membership only; launcher capabilities are not aggregated into the basic unit',
            'armor': 'card armor values are physical nominal thickness, never RHAe'}}


def fetch_snapshot(config_path_or_config, out_path=None, *, root=None, refresh=False, details=True, limit=None):
    if isinstance(config_path_or_config, dict):
        config = config_path_or_config
        root = Path(root or '.')
    else:
        cp = Path(config_path_or_config).resolve()
        config = json.loads(cp.read_text())
        root = Path(root or cp.parent.parent)
    catalog, audit = build_catalog(config, root, refresh=refresh, details=details, limit=limit)
    target = Path(out_path) if out_path else root / 'data/snapshots/catalog.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(catalog, ensure_ascii=False, separators=(',', ':')).encode('utf8')
    target.write_bytes(serialized)
    audit['catalog_sha256'] = hashlib.sha256(serialized).hexdigest()
    audit['coverage'] = catalog['snapshot']['coverage']
    report = root / 'reports/source-audit.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(catalog['snapshot']['coverage'], ensure_ascii=False), flush=True)
    return catalog


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default='config/sources.json')
    p.add_argument('--output')
    p.add_argument('--refresh', action='store_true')
    p.add_argument('--catalog-only', action='store_true')
    p.add_argument('--limit-details', type=int)
    args = p.parse_args()
    fetch_snapshot(args.config, args.output, refresh=args.refresh,
                   details=not args.catalog_only, limit=args.limit_details)


if __name__ == '__main__':
    main()
