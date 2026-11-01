"""Capture official linked BR sheets and audit their active, mode-specific cells.

Published tables identify vehicles by display name. Their audit never changes
catalog IDs or promotes a mixed client snapshot; exact-ID rule imports remain
the responsibility of rule_snapshot.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from lxml import html

from ..common import atomic_write, content_hash, file_hash, read_json, write_json


SHEETS = {'Aircraft': 'air', 'Ground Forces': 'ground'}
HEADERS = {
    'air': ['', 'Arcade', 'Air Realistic', 'Ground Realistic and Simulator', 'Air Simulator', 'Comments'],
    'ground': ['Ground Forces', 'Arcade', 'Realistic', 'Comments'],
}
MODES = {'air': ['arcade', 'air_rb', 'ground_rb', 'air_sb'], 'ground': ['arcade', 'ground_rb']}


def normalized(text):
    return ' '.join(text.split())


def document(payload):
    return html.fromstring(payload.decode('utf-8-sig'))


def publication_url(announcement):
    urls = set(document(announcement).xpath('//a/@href'))
    matches = sorted(url for url in urls if re.fullmatch(
        r'https://docs\.google\.com/spreadsheets/(?:u/\d+/)?d/e/[^/?#]+/pubhtml', url))
    if len(matches) != 1:
        raise ValueError('Announcement must link exactly one published BR workbook')
    return matches[0]


def sheet_urls(publication, root_url):
    # Decode only string literals in Google's published sheet directory; never
    # execute page JavaScript. JSON handles escaped slashes after hex decoding.
    pattern = r'items\.push\(\{name:\s*("(?:\\.|[^"\\])*"),\s*pageUrl:\s*("(?:\\.|[^"\\])*"),\s*gid:\s*"(-?\d+)"'
    def decode(value):
        return json.loads(re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: '\\u00' + m[1], value))
    result = {}
    for name, url, gid in re.findall(pattern, publication.decode('utf-8-sig')):
        name, url = decode(name), decode(url)
        if name not in SHEETS:
            continue
        domain = SHEETS[name]
        expected = root_url + '/sheet?headers=false&gid=' + gid
        if url != expected or domain in result:
            raise ValueError('Conflicting or external published sheet URL')
        result[domain] = url
    if set(result) != {'air', 'ground'}:
        raise ValueError('Published aircraft and ground sheet directory required')
    return result


def verify_capture(root):
    root = Path(root)
    receipt = read_json(root / 'capture.json')
    files = receipt['files']
    if set(files) != {'announcement', 'publication', 'air', 'ground'}:
        raise ValueError('Incomplete BR publication capture')
    payloads = {}
    for name, row in files.items():
        path = root / (name + '.html')
        if file_hash(path) != row['sha256'] or path.stat().st_size != row['bytes']:
            raise ValueError('BR publication capture changed: ' + name)
        payloads[name] = path.read_bytes()
    url = files['announcement']['url']; parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'warthunder.com':
        raise ValueError('Official announcement host required')
    published = publication_url(payloads['announcement'])
    if published != files['publication']['url']:
        raise ValueError('Publication is not linked by captured announcement')
    for domain, url in sheet_urls(payloads['publication'], published).items():
        if files[domain]['url'] != url:
            raise ValueError('Sheet is not linked by captured publication')
    return receipt, payloads


def capture_publication(root, announcement_url, *, offline=False):
    root = Path(root)
    if (root / 'capture.json').is_file():
        receipt, _ = verify_capture(root)
        if announcement_url and receipt['files']['announcement']['url'] != announcement_url:
            raise ValueError('Existing capture belongs to a different announcement')
        return receipt
    if offline:
        raise ValueError('No complete BR publication capture available offline')
    if not announcement_url or urlsplit(announcement_url).scheme != 'https' or urlsplit(announcement_url).netloc != 'warthunder.com':
        raise ValueError('Official HTTPS announcement URL required')
    files = {}
    def fetch(name, url):
        request = Request(url, headers={'User-Agent': 'WT-Ground-Atlas official BR capture'})
        with urlopen(request, timeout=30) as response:
            if response.url != url:
                raise ValueError('Unexpected BR publication redirect')
            payload = response.read(4 * 1024**2 + 1)
        if len(payload) > 4 * 1024**2:
            raise ValueError('BR publication exceeds byte budget')
        atomic_write(root / (name + '.html'), payload)
        files[name] = {'url': url, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        return payload
    announcement = fetch('announcement', announcement_url)
    published = publication_url(announcement)
    workbook = fetch('publication', published)
    for domain, url in sheet_urls(workbook, published).items():
        parse_sheet(fetch(domain, url), domain)
    receipt = {'schema_version': '1.0.0', 'collected_at': datetime.now(timezone.utc).isoformat(), 'files': files}
    write_json(root / 'capture.json', receipt)
    return verify_capture(root)[0]


def parse_sheet(payload, domain):
    if domain not in HEADERS:
        raise ValueError('Unsupported BR publication domain')
    root = document(payload)
    tables = root.xpath('//table[contains(concat(" ", normalize-space(@class), " "), " waffle ")]')
    if not tables:
        raise ValueError('Published sheet table required')
    strike_classes = set()
    for css in root.xpath('//style/text()'):
        for selector, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
            if re.search(r'text-decoration(?:-line)?\s*:[^;]*line-through', declarations):
                match = re.search(r'\.([\w-]+)\s*$', selector)
                if not match:
                    raise ValueError('Unsupported strikethrough stylesheet selector')
                strike_classes.add(match[1])
    def active(node):
        if (node.tag in ('s', 'strike', 'del') or set(node.get('class', '').split()) & strike_classes or
                re.search(r'text-decoration(?:-line)?\s*:[^;]*line-through', node.get('style', ''))):
            return ''
        return (node.text or '') + ''.join(active(child) + (child.tail or '') for child in node)
    # Google can split a single published sheet into several grid chunks.
    # Verify one sheet and contiguous source row numbers before combining them.
    rows = [row for table in tables for row in table.xpath('./tbody/tr[th/@id]')]
    identities = [re.fullmatch(r'(-?\d+)R(\d+)', row.xpath('./th/@id')[0]) for row in rows]
    if (not identities or any(m is None for m in identities) or
            len({m[1] for m in identities}) != 1 or
            [int(m[2]) for m in identities] != list(range(len(rows)))):
        raise ValueError('Missing, duplicate or mixed published sheet rows')
    if len(rows) < 2 or [normalized(x.text_content()) for x in rows[0].xpath('./td')] != HEADERS[domain]:
        raise ValueError('Unexpected BR mode headers')
    if [normalized(x.text_content()) for x in rows[1].xpath('./td')] != ['was', 'now'] * len(MODES[domain]):
        raise ValueError('Unexpected old/new BR subheaders')
    width = 2 * len(MODES[domain]) + 2
    expected_spans = [1] + [2] * len(MODES[domain]) + [1]
    if [int(x.get('colspan', 1)) for x in rows[0].xpath('./td')] != expected_spans:
        raise ValueError('Unexpected BR mode header spans')
    result = []; seen = set(); notes = False
    def rating(value):
        if not value:
            return None
        if not re.fullmatch(r'[1-9]\d*\.(?:0|3|7)', value):
            raise ValueError('Ambiguous or invalid active BR value: ' + value)
        return float(value)
    for tr in rows[2:]:
        row_id = tr.xpath('./th/@id')[0]
        if row_id in seen:
            raise ValueError('Duplicate source row identity')
        seen.add(row_id)
        cells = tr.xpath('./td')
        values = [normalized(active(td)) for td in cells]
        raw = [normalized(td.text_content()) for td in cells]
        row = {'row_id': row_id, 'domain': domain, 'cells': values, 'raw_cells': raw,
            'revision_markup_present': values != raw,
            'source_row_sha256': hashlib.sha256(html.tostring(tr, encoding='utf-8')).hexdigest()}
        if len(cells) == 1 and int(cells[0].get('colspan', 1)) == width and values[0].startswith('Concomitant balance changes'):
            notes = True
            row.update(kind='balance_heading', text=values[0])
        elif notes and len(cells) == 2 and int(cells[1].get('colspan', 1)) == width - 1:
            row.update(kind='balance_note', name=values[0], text=values[1])
        elif not notes and len(cells) == width and all(int(c.get('colspan', 1)) == 1 for c in cells):
            if not values[0] and raw[0]:
                row.update(kind='withdrawn', name=raw[0])
            else:
                if not values[0]:
                    raise ValueError('Unnamed BR publication row')
                changes = {}
                for i, mode in enumerate(MODES[domain]):
                    old, new = (rating(v) for v in values[1 + 2*i:3 + 2*i])
                    if (old is None) != (new is None):
                        raise ValueError('Incomplete old/new BR pair in ' + row_id)
                    changes[mode] = {'old': old, 'new': new}
                row.update(kind='rating', name=values[0], changes=changes, comment=values[-1])
        else:
            raise ValueError('Unrecognized BR publication row layout: ' + row_id)
        result.append(row)
    return {'domain': domain, 'source_sha256': hashlib.sha256(payload).hexdigest(),
        'source_chunk_count': len(tables), 'rows': result}


def audit_catalog(catalog, sheets):
    index = defaultdict(list)
    for vehicle in catalog['vehicles']:
        domain = 'ground' if vehicle['kind'] == 'ground' else 'air'
        index[(domain, normalized(vehicle['name']))].append(vehicle)
    output = []
    for sheet in sheets:
        duplicates = Counter(r['name'] for r in sheet['rows'] if r['kind'] == 'rating')
        for row in sheet['rows']:
            result = {**row, 'sheet_sha256': sheet['source_sha256']}
            if row['kind'] != 'rating':
                result['status'] = row['kind']
            elif row['changes']['ground_rb']['new'] is None:
                # An Air RB column is not proof of a Ground RB change, including
                # helicopter entries whose runtime mode fallback needs evidence.
                result['status'] = 'no_explicit_ground_rb_change'
            elif duplicates[row['name']] > 1:
                result['status'] = 'duplicate_publication_name'
            else:
                candidates = index[(row['domain'], row['name'])]
                result['candidate_ids'] = [v['id'] for v in candidates]
                if len(candidates) != 1:
                    result['status'] = 'ambiguous_catalog_name' if candidates else 'unmatched_catalog_name'
                else:
                    vehicle = candidates[0]
                    value = vehicle.get('br', {}).get('current')
                    result.update(vehicle_id=vehicle['id'], catalog_br=value,
                        identity_scope='exact_display_name_candidate_not_import_authority',
                        status='matches_current_rating' if value == row['changes']['ground_rb']['new'] else 'differs_from_current_rating')
            output.append(result)
    return {'schema_version': '1.0.0', 'catalog_sha256': content_hash(catalog), 'rows': output,
        'counts': dict(Counter(r['status'] for r in output)),
        'revision_rows': sum(r['revision_markup_present'] for r in output),
        'catalog_modified': False, 'promoted_release_status': False, 'publishable': False}
