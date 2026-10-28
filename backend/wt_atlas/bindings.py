"""Audit collision-node ↔ Datamine DamageParts matches without guessed defaults."""
from __future__ import annotations
from pathlib import Path
from .common import read_json, write_json, file_hash
from .geometry import decode_grp


def damageparts_audit(grp_path, unit_config_path, vendor, output, source_identity):
    unit = read_json(unit_config_path)
    if not isinstance(unit.get('DamageParts'), dict):
        raise ValueError('Unit config has no parsed DamageParts object')
    candidates = {}
    includes = []
    def visit(block, path, inherited, origins):
        scalars = {key: value for key, value in block.items() if not isinstance(value, (dict, list))}
        values = {**inherited, **scalars}
        provenance = {**origins, **{key: path + '/' + key for key in scalars}}
        for key, child in block.items():
            if key.startswith('@') or 'include' in key.lower():
                includes.append({'path': path + '/' + key, 'value': child})
            if isinstance(child, dict):
                child_scalars = {field: value for field, value in child.items() if not isinstance(value, (dict, list))}
                candidates.setdefault(key, []).append({'path': path + '/' + key,
                    'effective_source_fields': {**values, **child_scalars},
                    'field_origins': {**provenance, **{field: path + '/' + key + '/' + field for field in child_scalars}}})
                visit(child, path + '/' + key, values, provenance)
    visit(unit['DamageParts'], 'DamageParts', {}, {})
    decoded = decode_grp(grp_path, vendor)
    results = []
    for resource in decoded:
        nodes = []
        for node in resource['parsed']['nodes']:
            found = candidates.get(node['name'], [])
            nodes.append({'node': node['index'], 'name': node['name'], 'behavior_flags': node['behavior_flags'],
                          'binding_status': 'source_name_matched' if len(found) == 1 else 'ambiguous' if found else 'unmapped',
                          'candidates': found,
                          'loaded_runtime_material_status': 'not_derived_from_raw_fields'})
        results.append({'collision_resource': resource['resource']['name'], 'nodes': nodes,
                        'counts': {state: sum(node['binding_status'] == state for node in nodes)
                                   for state in ['source_name_matched', 'ambiguous', 'unmapped']}})
    report = {'schema_version': 1, 'grp_sha256': file_hash(grp_path), 'unit_config_sha256': file_hash(unit_config_path),
              'source_identity': source_identity, 'resources': results, 'unresolved_external_includes': includes,
              'physics_profile_ready': False,
              'scope': 'Nested source field inheritance and exact node-name matches only. Runtime defaults, external armor-class inheritance, binding exclusions and per-mechanism arrays still require verified resolution. No guessed armor quality, class flags or part IDs.'}
    write_json(output, report)
    return report
