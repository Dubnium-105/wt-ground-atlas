"""Capture current Wiki catalogs and refresh exact IDs without rebinding physics.

The Wiki has no game-version identifier. A successful capture updates observed
catalog facts, never certifies equivalence to a particular client or Datamine.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .forecast.data import canonical, digest, file_hash, read_json, safe_file
from .forecast.updates import update_report
from .sources import parse_catalog


CATALOG_FIELDS = ('name', 'nation', 'kind', 'role', 'wiki_role', 'rank', 'branch',
                  'folder', 'folder_members', 'predecessors', 'tree', 'special',
                  'acquisition', 'br', 'br_source_mode', 'source_battle_ratings',
                  'wiki_url', 'image_url')


def atomic_json(path, value):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink refresh output')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def capture_wiki(config, cache, *, offline=False, max_bytes=None):
    """Publish the capture manifest only after every configured catalog parses."""
    cache = Path(cache)
    cfg = config['wiki']
    max_bytes = cfg['catalog_max_bytes'] if max_bytes is None else max_bytes
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError('Invalid Wiki capture byte budget')
    expected = {cfg['base_url'].rstrip('/') + '/' + slug: kind for slug, kind in cfg['catalogs'].items()}
    if set(expected.values()) != {'ground', 'aircraft', 'helicopter'} or len(expected) != 3:
        raise ValueError('Refresh needs all three Wiki catalogs')
    if any(urlsplit(url).scheme != 'https' or urlsplit(url).hostname != 'wiki.warthunder.com' for url in expected):
        raise ValueError('Official Wiki catalog URLs required')
    manifest_path = cache/'latest.json'
    if offline:
        manifest = read_json(manifest_path)
        if {s['url']: s['kind'] for s in manifest['sources']} != expected:
            raise ValueError('Offline capture does not cover configured catalogs')
    else:
        manifest = {'schema_version': '1.0.0', 'collected_at': datetime.now(timezone.utc).isoformat(), 'sources': []}
        for url, kind in expected.items():
            with urlopen(Request(url, headers={'User-Agent': cfg['user_agent']}), timeout=cfg['timeout_s']) as response:
                if urlsplit(response.url).hostname != 'wiki.warthunder.com':
                    raise ValueError('Wiki redirected to another host')
                raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError('Wiki capture byte budget exceeded')
            # Refuse a partial/empty response before replacing the last receipt.
            rows = parse_catalog(raw.decode('utf-8'), kind, cfg, url)
            if not rows:
                raise ValueError('Empty Wiki catalog')
            sha = hashlib.sha256(raw).hexdigest()
            target = cache/(sha + '.html')
            cache.mkdir(parents=True, exist_ok=True)
            if any(p.is_symlink() for p in (target, *target.parents)):
                raise ValueError('Symlink capture output')
            if not target.exists() or file_hash(target) != sha:
                fd, temporary = tempfile.mkstemp(prefix='.capture-', dir=cache)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                    os.replace(temporary, target)
                finally:
                    if Path(temporary).exists(): Path(temporary).unlink()
            manifest['sources'].append({'url': url, 'kind': kind, 'path': target.name, 'sha256': sha,
                                        'bytes': len(raw), 'count': len(rows), 'br_field': cfg['br_fields'][kind]})
    vehicles = []
    for source in manifest['sources']:
        path = safe_file(cache, source['path'])
        if path.stat().st_size > max_bytes or path.stat().st_size != source['bytes'] or file_hash(path) != source['sha256']:
            raise ValueError('Wiki capture checksum/size mismatch')
        rows = parse_catalog(path.read_text(encoding='utf-8'), source['kind'], cfg, source['url'])
        if len(rows) != source['count'] or not rows:
            raise ValueError('Wiki capture row-count mismatch')
        vehicles.extend(rows)
    if len({v['id'] for v in vehicles}) != len(vehicles):
        raise ValueError('Duplicate exact ID across Wiki catalogs')
    if not offline:
        atomic_json(manifest_path, manifest)
    return manifest, vehicles


def refresh_catalog(before, manifest, vehicles):
    """Update captured catalog fields, keeping exact-ID physical facts separate."""
    old = {v['id']: v for v in before['vehicles']}
    if len(old) != len(before['vehicles']) or len({v['id'] for v in vehicles}) != len(vehicles):
        raise ValueError('Duplicate catalog identity')
    result = copy.deepcopy(before)
    refreshed, changes, added, unobserved, invalidated = [], [], [], [], []
    sources = {s['kind']: s for s in manifest['sources']}
    for observed in vehicles:
        identifier = observed['id']; previous = old.get(identifier)
        if previous and (previous['kind'] != observed['kind'] or previous['nation'] != observed['nation']):
            raise ValueError('Exact-ID nation/platform conflict: ' + identifier)
        row = copy.deepcopy(previous or observed)
        for field in CATALOG_FIELDS:
            row[field] = copy.deepcopy(observed[field])
        row['catalog_presence'] = 'observed_current_wiki'
        source = sources[row['kind']]
        row['br_provenance'] = {'source': source['url'], 'source_version': None,
            'status': 'captured_unversioned', 'sha256': source['sha256'],
            'collected_at': manifest['collected_at'], 'mode': observed['br_source_mode']}
        if previous:
            if previous['br'].get('current') != row['br'].get('current'):
                changes.append({'vehicle_id': identifier, 'old': previous['br'].get('current'),
                                'current': row['br'].get('current'), 'mode': row['br_source_mode']})
            # Catalog pages do not revalidate carried presets, custom BR rules or
            # sortie costs. Preserve the evidence but quarantine stale legality.
            stale = {k: copy.deepcopy(previous[k]) for k in ('rating_configurations', 'spawn_rule') if k in previous}
            if stale:
                row['previous_snapshot_rules'] = stale
                row.pop('spawn_rule', None)
                for block in row.get('rating_configurations', {}).values():
                    block['status'] = 'unvalidated'
            for loadout in row.get('loadouts', []):
                loadout['previous_snapshot_legal'] = loadout.get('legal')
                loadout.update(legal=False, source_status='unvalidated',
                               refresh_blocker='current_catalog_capture_does_not_revalidate_loadout')
            if stale or row.get('loadouts'):
                invalidated.append(identifier)
        else:
            added.append(identifier)  # No copied ammunition, geometry or assumed payload.
        refreshed.append(row)
    seen = {v['id'] for v in vehicles}
    for identifier in sorted(old.keys() - seen):
        row = copy.deepcopy(old[identifier]); row['br'] = {'current': None, 'planned': None}
        row['catalog_presence'] = 'not_observed_in_current_capture'
        # Absence from the Wiki is not proof that an owned vehicle was removed.
        for block in row.get('rating_configurations', {}).values(): block['status'] = 'unvalidated'
        refreshed.append(row); unobserved.append(identifier)
    result['vehicles'] = refreshed
    nation_labels = {n['id']: n.get('label', n['id']) for n in before.get('nations', [])}
    result['nations'] = [{'id': n, 'label': nation_labels.get(n, n)}
                         for n in dict.fromkeys(v['nation'] for v in refreshed)]
    result.pop('forecast_scenario', None)
    snapshot = result.setdefault('snapshot', {})
    snapshot.update(id='wiki-' + digest(manifest)[:16], snapshot_id='wiki-' + digest(manifest)[:16],
        game_version=None, source_status='unknown', effective_date=None,
        collected_at=manifest['collected_at'], wiki_collected_at=manifest['collected_at'],
        captures=copy.deepcopy(manifest['sources']), sources=copy.deepcopy(manifest['sources']),
        version_note='Current Wiki capture is unversioned; retained physical provenance is not a same-version assertion.')
    snapshot['coverage'] = {
        'catalog_vehicles': len(refreshed), 'current_wiki_observed': len(vehicles),
        'kinds': {kind: sum(v['kind'] == kind for v in refreshed) for kind in sources},
        'details_parsed': 0, 'previous_snapshot_details_retained': len(old),
        'ground_rb_known': sum(v['br'].get('current') is not None for v in refreshed),
        'with_ammunition': sum(bool(v.get('ammo')) for v in refreshed),
        'with_legal_loadout': sum(any(l.get('legal') is True for l in v.get('loadouts', [])) for v in refreshed),
        'legal_whole_presets': sum(l.get('legal') is True for v in refreshed for l in v.get('loadouts', [])),
        'quarantined_presets': sum(l.get('source_status') == 'unvalidated' for v in refreshed for l in v.get('loadouts', [])),
        'special_vehicles': sum(v.get('special') is True for v in refreshed),
    }
    receipt = {'schema_version': '1.0.0', 'before_sha256': digest(before), 'captures': manifest,
               'observed_vehicles': len(vehicles), 'updated_ratings': changes, 'added_ids': added,
               'unobserved_ids': unobserved, 'rules_requiring_revalidation': invalidated,
               'physical_sources_copied_by_exact_id_only': True,
               'game_version_alignment_verified': False, 'future_snapshot_created': False}
    result['catalog_refresh'] = {k: v for k, v in receipt.items() if k not in ('updated_ratings', 'captures')}
    receipt['after_sha256'] = digest(result)
    receipt['changes'] = update_report(before, result)
    return result, receipt
