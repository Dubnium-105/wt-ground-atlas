"""Read public Tencent Sheets cell blocks, preserve rows, and audit stable-ID maps.

Wire tags are serialization constants, not gameplay values. Numeric pool behavior
is verified against Tencent's served initial_chunk_pb_4 client (module 458782).
No source row is applied unless its vehicle and Ground RB columns are unambiguous.
"""
from __future__ import annotations
import argparse
import base64
import collections
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import unicodedata
import urllib.parse
import zlib
from lxml import html
from .sources import fetch_bytes


def varint(data, offset):
    value = shift = 0
    while offset < len(data):
        byte = data[offset]; offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
        shift += 7
        if shift > 70:
            raise ValueError('Protobuf varint overflow')
    raise ValueError('Truncated protobuf varint')


def wire(data):
    result = []
    offset = 0
    while offset < len(data):
        key, offset = varint(data, offset)
        field, typ = key >> 3, key & 7
        if not field:
            raise ValueError('Invalid protobuf field')
        if typ == 0:
            value, offset = varint(data, offset)
        elif typ == 1:
            value = struct.unpack('<d', data[offset:offset + 8])[0]; offset += 8
        elif typ == 2:
            length, offset = varint(data, offset)
            value = data[offset:offset + length]; offset += length
        elif typ == 5:
            value = data[offset:offset + 4]; offset += 4
        else:
            raise ValueError(f'Unsupported protobuf wire type {typ}')
        if offset > len(data):
            raise ValueError('Truncated protobuf field')
        result.append((field, typ, value))
    return result


def first(fields, key, default=None):
    return next((v for n, _, v in fields if n == key), default)


def repeated(fields, key):
    return [v for n, _, v in fields if n == key]


def opendoc_json(raw):
    source = raw.decode() if isinstance(raw, bytes) else raw
    if source.lstrip().startswith('{'):
        return json.loads(source)
    return json.loads(source[source.index('(') + 1:source.rindex(')')])


def extract_blocks(doc):
    records = doc['clientVars']['collab_client_vars']['initialAttributedText']['text']
    return [r for r in records if isinstance(r, dict)]


def decode_sheet(data, numeric_inline_max):
    root = wire(first(wire(data), 1))
    rows = collections.defaultdict(dict)
    for envelope in repeated(root, 5):
        message = wire(envelope)
        if first(message, 1) != 18:
            continue
        payload = wire(first(message, 19))
        dictionary = wire(first(payload, 5))
        strings = [first(wire(v), 1, b'').decode() for v in repeated(dictionary, 1)]
        floats = [first(wire(v), 1) for v in repeated(dictionary, 3)]
        for raw_cell in repeated(payload, 6):
            cell = wire(raw_cell)
            row, col = first(cell, 1, 0), first(cell, 2, 0)
            value = wire(first(cell, 3, b''))
            typ = first(value, 1)
            if typ not in (2, 4):
                continue
            index = first(wire(first(value, 2, b'')), 1, 0)
            if typ == 4:
                parsed = strings[index]
            else:
                parsed = index if index <= numeric_inline_max else floats[index - numeric_inline_max - 1]
            rows[row][col] = parsed
    return {str(k): {str(col): val for col, val in sorted(v.items())} for k, v in sorted(rows.items())}


def workbook_sheets(data):
    root = wire(first(wire(data), 1))
    sheets = []
    for e in repeated(root, 5):
        m = wire(e)
        if first(m, 1) != 1:
            continue
        s = wire(first(m, 2))
        sheets.append({'id': first(wire(first(s, 3)), 1).decode(),
            'name': first(wire(first(s, 5)), 1).decode(),
            'rows': first(wire(first(s, 7)), 1, 0),
            'columns': first(wire(first(s, 8)), 1, 0)})
    return sheets


def normalized_name(s):
    s = unicodedata.normalize('NFKC', s).casefold()
    return ''.join(ch for ch in s if ch.isalnum())


def aliases_from_datamine(path, vehicles):
    ids = {v['id'] for v in vehicles}
    result = collections.defaultdict(set)
    for v in vehicles:
        result[normalized_name(v['name'])].add(v['id'])
        result[normalized_name(v['id'])].add(v['id'])
    with Path(path).open(encoding='utf-8-sig') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            key = row.get('<ID|readonly|noverify>', '')
            vid = re.sub(r'_(shop|0|1)$', '', key)
            if vid not in ids or vid == key:
                continue
            for lang in ['<English>', '<Chinese>', '<TChinese>', '<HChinese>']:
                name = row.get(lang)
                if name:
                    result[normalized_name(name)].add(vid)
    return result


def map_changes(sheet_rows, catalog, aliases, config):
    entries, audit = [], []
    by_id = {v['id']: v for v in catalog['vehicles']}
    by_key = {}
    nation_names = {label: key for key, label in config['wiki']['nation_labels'].items()}
    # These are normalization aliases to source country labels, not vehicle maps.
    nation_names.update(config['changes'].get('country_aliases', {}))
    for sheet in sheet_rows:
        rows = sheet['rows']
        kind = sheet['kind']
        headers = rows.get('0', {})
        header_target = config['changes']['column_headers'][kind]
        starts = [int(col) for col, val in headers.items() if val == header_target]
        if len(starts) != 1:
            audit.append({'status': 'unrecognized_ground_rb_columns', 'sheet': sheet['id'], 'headers': headers})
            continue
        old_col = starts[0]
        subheaders = rows.get('1', {})
        if subheaders.get(str(old_col)) != config['changes']['before_header'] or subheaders.get(str(old_col + 1)) != config['changes']['after_header']:
            audit.append({'status': 'unrecognized_before_after_columns', 'sheet': sheet['id']})
            continue
        for row_num, row in rows.items():
            name = row.get('0')
            old, new = row.get(str(old_col)), row.get(str(old_col + 1))
            if not isinstance(name, str) or not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
                continue
            record = {'sheet': sheet['id'], 'row_index': int(row_num), 'row_number': int(row_num) + 1,
                'source_name': name, 'raw_cells': row, 'old': old, 'new': new}
            if old == new:
                record['status'] = 'no_ground_rb_change'; audit.append(record); continue
            candidates = set(aliases.get(normalized_name(name), set()))
            country = None
            suffix = re.search(r'[（(]([^）)]+)[）)]\s*$', name)
            base_name = name
            if suffix and suffix.group(1) in nation_names:
                country = nation_names[suffix.group(1)]
                base_name = name[:suffix.start()].strip()
                candidates |= aliases.get(normalized_name(base_name), set())
            candidates = {i for i in candidates if (by_id[i]['kind'] == 'ground' if kind == 'ground' else by_id[i]['kind'] in ('aircraft','helicopter'))}
            if country:
                candidates = {i for i in candidates if by_id[i]['nation'] == country}
            record['candidate_ids'] = sorted(candidates)
            if len(candidates) != 1:
                record['status'] = 'unmapped' if not candidates else 'ambiguous'
                audit.append(record); continue
            identifier = next(iter(candidates))
            record['vehicle_id'] = identifier
            current = by_id[identifier]['br']['current']
            if current != old:
                record['status'] = 'baseline_conflict'; record['baseline_br'] = current
                audit.append(record); continue
            key = (identifier, 'br')
            if key in by_key:
                record['status'] = 'duplicate' if by_key[key]['new'] == new else 'conflicting_duplicate'
                audit.append(record)
                if by_key[key]['new'] != new:
                    entries.remove(by_key[key]); by_key.pop(key)
                continue
            entry = {'vehicle_id': identifier, 'field': 'br', 'old': old, 'new': new,
                'source_row': record, 'description': f'{name}: {old} → {new}', 'simulated': True}
            entries.append(entry); by_key[key] = entry
            record['status'] = 'accepted'; audit.append(record)
            # Text changes remain text/audit. They are never converted to invented parameters.
        for row_num, row in rows.items():
            for col, val in row.items():
                if isinstance(val, str) and col != '0' and len(val) > config['changes']['text_change_min_chars']:
                    audit.append({'status': 'text_only_change', 'sheet': sheet['id'],
                        'row_number': int(row_num) + 1, 'column_index': int(col), 'text': val})
    return entries, audit


def fetch_changes(config_path, catalog_path=None, *, refresh=False):
    cp = Path(config_path).resolve(); root = cp.parent.parent
    config = json.loads(cp.read_text()); cfg = config['wiki']; cc = config['changes']
    cache = root / config['cache']['raw_directory']
    public_headers = {'User-Agent': cc['user_agent'], 'Referer': cc['url']}
    source = fetch_bytes(cc['url'], cache/'qq_sheet.html', cfg, refresh=refresh, headers=public_headers)
    document = html.fromstring(source)
    links = [u for u in document.xpath('//link/@href') if '/opendoc?' in u]
    if not links:
        raise ValueError('Public sheet has no opendoc link')
    url = ('https:' if links[0].startswith('//') else '') + links[0]
    original = opendoc_json(fetch_bytes(url, cache/'qq_opendoc.jsonp', cfg, refresh=refresh, headers=public_headers))
    blocks = extract_blocks(original)
    sheets = workbook_sheets(zlib.decompress(base64.b64decode(blocks[0]['workbook'])))
    exported = []
    for kind, identifier in [('ground', cc['ground_tab']), ('aircraft', cc['aviation_tab'])]:
        sm = next(s for s in sheets if s['id'] == identifier)
        if identifier == cc['ground_tab']:
            u = url
        else:
            page_url = re.sub(r'tab=[^&]+', 'tab=' + identifier, cc['url'])
            page = fetch_bytes(page_url, cache/f'qq_page_{identifier}.html', cfg, refresh=refresh, headers=public_headers)
            page_doc = html.fromstring(page)
            public_links = [x for x in page_doc.xpath('//link/@href') if '/opendoc?' in x]
            if not public_links:
                raise ValueError('Public sheet tab did not provide its data URL')
            u = ('https:' if public_links[0].startswith('//') else '') + public_links[0]
        u = re.sub(r'([?&])endrow=\d+', lambda m: m.group(1) + 'endrow=' + str(sm['rows']), u)
        u = re.sub(r'block_end_row=\d+', 'block_end_row=' + str(sm['rows'] - 1), u)
        raw = fetch_bytes(u, cache/f'qq_full_{identifier}.jsonp', cfg, refresh=refresh, headers=public_headers)
        doc = opendoc_json(raw)
        rows = {}
        for block in extract_blocks(doc):
            for item in block.get('block_datas', []):
                if not item.get('related_sheet'):
                    continue
                decoded = zlib.decompress(base64.b64decode(item['related_sheet']))
                rows.update(decode_sheet(decoded, cc['numeric_inline_max']))
        exported.append({'id': identifier, 'name': sm['name'], 'kind': kind,
            'expected_rows': sm['rows'], 'captured_rows': len(rows),
            'last_nonempty_row_index': max(map(int, rows)) if rows else None, 'rows': rows,
            'source_sha256': hashlib.sha256(raw).hexdigest()})
    cp = Path(catalog_path) if catalog_path else root/'data/snapshots/catalog.json'
    catalog = json.loads(cp.read_text())
    dm = config['datamine']; path = dm['localization_path']
    raw_names = fetch_bytes(f"{dm['raw_base']}/{dm['ref']}/{path}", root/'data/raw/datamine'/path, cfg)
    aliases = aliases_from_datamine(root/'data/raw/datamine'/path, catalog['vehicles'])
    entries, audit = map_changes(exported, catalog, aliases, config)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    output = {'schema_version': config['schema_version'], 'id': 'tencent-'+hashlib.sha256(source).hexdigest()[:16],
        'status': 'simulated', 'source_url': cc['url'], 'collected_at': now,
        'title': original['clientVars'].get('padTitle'), 'entries': entries, 'audit': audit,
        'coverage': {'accepted_changes': len(entries), 'audit_statuses': dict(collections.Counter(r['status'] for r in audit)),
                     'sheet_rows': {s['id']: s['captured_rows'] for s in exported}},
        'provenance': {'localization_commit': dm['ref'], 'localization_sha256': hashlib.sha256(raw_names).hexdigest(),
            'numeric_dictionary_source': cc['numeric_dictionary_source'],
            'note': 'Ground RB columns only. Plan is simulated; conflicting baseline and ambiguous aliases never auto-apply.'}}
    target = root/'data/snapshots/changes.json'
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    (root/'data/snapshots/changes-raw-rows.json').write_text(json.dumps(exported, ensure_ascii=False, indent=2))
    print(json.dumps(output['coverage'],ensure_ascii=False))
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='config/sources.json');p.add_argument('--catalog');p.add_argument('--refresh',action='store_true')
    a=p.parse_args();fetch_changes(a.config,a.catalog,refresh=a.refresh)

if __name__=='__main__':main()
