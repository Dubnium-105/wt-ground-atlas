"""Reconcile unresolved import records with the explicitly scoped gap list."""
from collections import Counter


def reconcile(gaps, summary, recovery, catalog):
    if recovery['source_version'] != gaps['source_version']:
        raise ValueError('Source version mismatch')
    if recovery['output_catalog_sha256'] != summary['catalog_sha256']:
        raise ValueError('Catalog identity mismatch')
    ids = gaps['ground_ammunition']['vehicles_without_any_exact_owned_profile']
    if len(ids) != len(set(ids)) or len(ids) != summary['without_owned_supported_source_profile']:
        raise ValueError('Ground gap identities do not match summary')
    vehicles = {v['id']: v for v in catalog['vehicles']}
    if len(vehicles) != len(catalog['vehicles']) or set(ids) - vehicles.keys():
        raise ValueError('Missing or duplicated catalog identities')
    rows = {identifier: [] for identifier in ids}
    seen = set()
    for record in recovery['records']:
        identifier = record['vehicle_id']
        if identifier not in rows or record['status'] != 'unresolved':
            continue
        key = (identifier, record['ammo_id'], record['reason'])
        if key in seen:
            raise ValueError('Duplicate unresolved selection record')
        seen.add(key)
        rows[identifier].append({'ammo_id': record['ammo_id'], 'reason': record['reason']})
    reasons = Counter()
    for records in rows.values():
        reasons.update({record['reason'] for record in records})
    if len(seen) != summary['unresolved_selection_records'] or dict(reasons) != summary['vehicle_counts_by_raw_reason']:
        raise ValueError('Selection records do not match gap summary')
    result = []
    for identifier in sorted(ids):
        records = sorted(rows[identifier], key=lambda x: (x['ammo_id'], x['reason']))
        vehicle = vehicles[identifier]
        system = vehicle.get('multi_vehicle_system') or {}
        result.append({'vehicle_id': identifier, 'name': vehicle.get('name'), 'wiki_url': vehicle.get('wiki_url'),
                       'unresolved_selections': records,
                       'system_role': vehicle.get('system_role'),
                       'system_capabilities_aggregated': system.get('capabilities_aggregated'),
                       'system_members': system.get('members', []),
                       'record_status': 'unresolved_selections_recorded' if records else 'no_unresolved_selection_record'})
    return {'schema_version': '1.0.0', 'source_version': recovery['source_version'],
            'scope': 'Vehicles without a supported owned profile in this snapshot. An importer reason is not proof of a missing external file, ground-target capability, or a particular fix. No record does not establish why a vehicle lacks a profile.',
            'vehicle_count': len(result), 'vehicles_with_unresolved_records': sum(bool(x) for x in rows.values()),
            'vehicles_without_unresolved_records': sum(not x for x in rows.values()),
            'unresolved_selection_records': len(seen), 'reason_vehicle_counts': dict(sorted(reasons.items())),
            'vehicles': result, 'formal_rankings': 0, 'publishable': False}
