"""Pinned client sortie-cost components, without inventing native base costs.

Port of ranks_common_shared.nut's parameter, carried-weapon multiplier and
weapon-dependent spawn-category functions. Final shop_get_spawn_score is native
and is deliberately not reconstructed from these components alone.
"""
from collections import Counter
import hashlib
from pathlib import Path

from .data import number, safe_file
from .rule_snapshot import verify_capture, RANKS_PATH, RANKS_SCRIPT_SHA256, WPCOST_PATH
from ..common import file_hash, read_json


MASS_FIELDS = ('totalBombRocketMass', 'totalNapalmBombMass', 'totalGuidedBombMass', 'totalTorpedoMass')
SURFACE_FIELDS = (*MASS_FIELDS, 'atgmVisibilityType')
MATH_SCRIPT_SHA256 = 'a476b527804516163ba9fe2fc1664742eff0700b1ce2b2471659dd3131b5e613'


def script_digest(path):
    # Extraction emits CRLF on Windows; reconcile only this byte-level change.
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def entries(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def nonnegative(value):
    if not number(value) or value < 0: raise ValueError('Invalid native spawn component')
    return value


def interpolate(points, value):
    """Client table interpolation clamps to the end values."""
    nonnegative(value)
    if (not isinstance(points, list) or not points or any(not isinstance(p, list) or len(p) != 2 or
            not all(number(x) and x >= 0 for x in p) for p in points) or
            any(b[0] <= a[0] for a, b in zip(points, points[1:]))):
        raise ValueError('Invalid native spawn interpolation table')
    if value <= points[0][0]: return points[0][1]
    for a, b in zip(points, points[1:]):
        if value <= b[0]: return a[1] + (b[1] - a[1]) * (value - a[0]) / (b[0] - a[0])
    return points[-1][1]


def score_param(rules, name, default, *, mode='TankHistorical', mission=None):
    mission = mission or {}; block = rules.get(mode, {})
    rank = mission.get('ranks', {}).get('max', 0); override = {}
    for row in block.get('override_params_by_session_rank', {}).values():
        if row.get('minMRank', -1) <= rank <= row.get('maxMRank', -1):
            override = row; break
    for source in (mission.get('customSpawnScore', {}), override,
                   override.get('respawn_cost_mul_by_exp_class', {}), block,
                   block.get('respawn_cost_mul_by_exp_class', {}), rules):
        if source.get(name) is not None: return source[name]
    return default


def weapon_param(rules, unit_name, unit_class, name):
    root = rules.get('WeaponMul', {})
    for key in (unit_name, unit_class, 'Common'):
        source = root.get(key, {})
        if source.get(name) is not None: return source[name]
    return None


def preset_parameters(unit, preset, *, custom=None):
    weapons = unit.get('weapons', {})
    if isinstance(weapons.get(preset), dict):
        parts = [(weapons[preset], 1)]
    elif custom:
        if not isinstance(custom, dict): raise ValueError('Expected an exact custom preset table')
        parts = []
        for name, count in custom.items():
            if name not in weapons or not isinstance(weapons[name], dict):
                raise ValueError('Unknown custom weapon identity')
            if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                raise ValueError('Invalid custom weapon quantity')
            parts.append((weapons[name], count))
    else: raise ValueError('Exact fixed or custom preset is required')
    mass = {k: 0. for k in (*MASS_FIELDS, 'maxRocketTntMass')}
    atgm = {'visibilityTypeArr': [], 'maxDistance': 0., 'hasProximityFuse': False}
    guided = {'visibilityTypeArr': []}; aam = {'guidanceTypeArr': []}
    surface = False
    for part, count in parts:
        for field in MASS_FIELDS: mass[field] += nonnegative(part.get(field, 0)) * count
        mass['maxRocketTntMass'] = max(mass['maxRocketTntMass'], nonnegative(part.get('maxRocketTntMass', 0)))
        atgm['maxDistance'] = max(atgm['maxDistance'], nonnegative(part.get('atgmMaxDistance', 0)))
        proximity = part.get('atgmHasProximityFuse', False)
        if not isinstance(proximity, bool): raise ValueError('Invalid native proximity-fuse flag')
        atgm['hasProximityFuse'] |= proximity
        for field, target, key in (('atgmVisibilityType', atgm, 'visibilityTypeArr'),
                                  ('guidedBombVisibilityType', guided, 'visibilityTypeArr'),
                                  ('aamGuidanceType', aam, 'guidanceTypeArr')):
            for value in entries(part.get(field)):
                if not isinstance(value, str): raise ValueError('Invalid native weapon guidance identity')
                if value and value not in target[key]: target[key].append(value)
        # The source tests field presence, not mass > 0. Preserve that distinction.
        surface |= any(part.get(k) is not None for k in SURFACE_FIELDS)
    return {'mass': mass, 'atgm': atgm, 'guided': guided, 'aam': aam, 'has_surface_weapon': surface}


def weapon_multiplier(rules, unit_name, unit_class, params):
    param = lambda name: weapon_param(rules, unit_name, unit_class, name)
    mass, atgm, guided, aam = (params[k] for k in ('mass', 'atgm', 'guided', 'aam'))
    combined, maximum = 1., 1.
    for field, name in (('totalBombRocketMass', 'BombRocketWeapon'),
                        ('totalNapalmBombMass', 'NapalmBombWeapon'), ('totalTorpedoMass', 'TorpedoWeapon')):
        table = param(name)
        if mass[field] > 0 and table is not None and table.get('mass') is not None:
            points = table['mass']; combined += interpolate(points, mass[field]) - 1
            maximum = max(maximum, points[-1][1])
    result = min(combined, maximum)
    table = param('GuidedBombWeapon')
    if mass['totalGuidedBombMass'] > 0 and table is not None and table.get('mass') is not None:
        visibility = param('GuidedBombVisibilityTypeMul') or {}
        mul = max([1.] + [nonnegative(visibility.get(t, 0)) for t in guided['visibilityTypeArr']])
        result = max(result, interpolate(table['mass'], mass['totalGuidedBombMass']) * mul)
    if atgm['visibilityTypeArr']:
        visibility = param('AtgmVisibilityTypeMul') or {}
        result = max([result] + [nonnegative(visibility.get(t, 0)) for t in atgm['visibilityTypeArr']])
        table = param('AtgmMaxDistanceMul')
        if atgm['maxDistance'] > 0 and table is not None and table.get('dist') is not None:
            result *= interpolate(table['dist'], atgm['maxDistance'])
        multiplier = param('atgmHasProximityFuseMul')
        if atgm['hasProximityFuse'] and multiplier is not None: result *= nonnegative(multiplier)
    guidance = param('AamGuidanceTypeMul') or {}
    result = max([result] + [nonnegative(guidance.get(t, 0)) for t in aam['guidanceTypeArr']])
    threshold, multiplier = param('largeRocketTntMass'), param('largeRocketMul')
    if mass['maxRocketTntMass'] > 0 and threshold is not None and multiplier is not None and mass['maxRocketTntMass'] >= nonnegative(threshold):
        result = max(result, nonnegative(multiplier))
    return nonnegative(result)


def belt_multiplier(unit, selected_belts):
    if (not isinstance(selected_belts, list) or any(not isinstance(v, str) or not v for v in selected_belts) or
            len(set(selected_belts)) != len(selected_belts)):
        raise ValueError('Unique explicit belt or gun identities required')
    result = 1.; charged = False
    for name in selected_belts:
        modification = unit.get('modifications', {}).get(name)
        default = unit.get('defaultBeltParam', {}).get(name)
        if modification is None and default is None: raise ValueError('Unknown selected belt or gun identity')
        value = (modification or {}).get('spawnCostMul')
        if value is None: value = (default or {}).get('spawnCostMul', 1.)
        nonnegative(value); result += value - 1.; charged |= value > 1.
    return nonnegative(result), charged


def cost_components(rules, unit_name, unit, preset, *, selected_belts=None, custom=None,
                    mode='TankHistorical', mission=None):
    unit_class = unit.get('unitClass')
    if not isinstance(unit_class, str): raise ValueError('Exact source unit class required')
    params = preset_parameters(unit, preset, custom=custom)
    use_weapons = score_param(rules, 'useSpawnCostMulForWeapon', False, mode=mode, mission=mission)
    use_bullets = score_param(rules, 'useSpawnCostMulForBullet', False, mode=mode, mission=mission)
    if not isinstance(use_weapons, bool) or not isinstance(use_bullets, bool): raise ValueError('Invalid native spawn switches')
    wm = weapon_multiplier(rules, unit_name, unit_class, params) if use_weapons else 1.
    # Unknown selected belts are harmless only if every declared option is free.
    choices = [row.get('spawnCostMul', 1.) for key in ('modifications', 'defaultBeltParam')
               for row in unit.get(key, {}).values() if isinstance(row, dict)]
    choices = [nonnegative(v) for v in choices]
    known = not use_bullets or selected_belts is not None or all(v == 1. for v in choices)
    bm, charged = belt_multiplier(unit, selected_belts or []) if known and use_bullets else (1., False)
    group = None
    if (mission or {}).get('useSpawnTypeByWeaponForSpawnScore') is True and unit_class in ('exp_fighter', 'exp_assault', 'exp_bomber'):
        if params['has_surface_weapon'] or (known and charged): group = 'bomber'
        elif known: group = 'exp_fighter'
    elif isinstance(unit.get('spawnType'), str): group = unit['spawnType']
    return {'weapon_multiplier': wm, 'bullet_multiplier': bm if known else None,
        'combined_multiplier': 1 + (wm - 1) + (bm - 1) if known else None,
        'selected_belts': selected_belts, 'bullet_selection_verified': known,
        'spawn_group': group, 'parameters': params,
        'base_class_coefficient': score_param(rules, unit_name,
            score_param(rules, unit_class, None, mode=mode, mission=mission), mode=mode, mission=mission),
        'base_respawn_points_coefficient': score_param(rules, 'base_respawn_points_coef', None, mode=mode, mission=mission),
        'respawn_power': score_param(rules, 'spawn_pow', 1., mode=mode, mission=mission),
        'final_spawn_points': None, 'status': 'verified_client_components_not_final_cost',
        'missing': ['native_base_room_formula_and_final_rounding'] + ([] if known else ['selected_internal_belts'])}


def import_components(catalog_path, rules_root, capture_path):
    catalog = read_json(catalog_path); snapshot = catalog['snapshot']; root = Path(rules_root)
    commit = snapshot.get('datamine_reference'); receipt = verify_capture(root, commit)
    version = (root / 'version').read_text(encoding='utf-8').strip()
    if snapshot.get('datamine_game_version') != version or file_hash(root / RANKS_PATH) != RANKS_SCRIPT_SHA256:
        raise ValueError('Unreviewed spawn script or mismatched catalog version')
    capture = read_json(capture_path); gui = capture['packs']['gui.vromfs.bin']
    if gui['version'] != version or file_hash(gui['saved']) != gui['sha256']:
        raise ValueError('Captured GUI pack hash or version mismatch')
    gui_root = Path(gui['extracted'])
    captured_script = safe_file(gui_root, 'globals/ranks_common_shared.nut')
    captured_math = safe_file(gui_root, 'std/math.nut')
    if (script_digest(captured_script) != RANKS_SCRIPT_SHA256 or
        script_digest(captured_math) != MATH_SCRIPT_SHA256):
        raise ValueError('Unreviewed captured spawn or interpolation script')
    shop = read_json(root / WPCOST_PATH); rules_path = root / 'char.vromfs.bin_u/config/warpoints.blkx'
    rules = read_json(rules_path)['respawn_points']; records = []
    source = {'source_version': version, 'datamine_commit': commit,
        'source': receipt['files'][RANKS_PATH]['url'], 'script_sha256': file_hash(root / RANKS_PATH),
        'captured_script_sha256': file_hash(captured_script),
        'interpolation_script_sha256': file_hash(captured_math), 'interpolation_lf_sha256': MATH_SCRIPT_SHA256,
        'script_reconciliation': 'CRLF_to_LF_only', 'client_capture_sha256': file_hash(capture_path),
        'rules_sha256': file_hash(rules_path), 'wpcost_sha256': file_hash(root / WPCOST_PATH),
        'capture_sha256': file_hash(root / 'capture.json')}
    for vehicle in catalog['vehicles']:
        binding = vehicle.get('rule_snapshot_reference', {}).get('binding', {})
        source_id = binding.get('source_id')
        if source_id not in shop: continue
        unit = shop[source_id]
        vehicle['native_spawn_parameters'] = {'unit_class': unit.get('unitClass'), 'spawn_type': unit.get('spawnType'),
            'base_class_coefficient': score_param(rules, source_id, score_param(rules, unit.get('unitClass'), None)),
            'respawn_power': score_param(rules, 'spawn_pow', 1.), 'source': source}
        for loadout in vehicle.get('loadouts', []):
            loadout.pop('spawn_cost_components', None)
            if loadout.get('legal') is not True or loadout.get('source_status') != 'official_verified': continue
            if loadout.get('source_version') != version: raise ValueError('Payload/spawn component version mismatch')
            row = {'vehicle_id': vehicle['id'], 'loadout_id': loadout['id']}
            try:
                # No mission flag is assumed from the vehicle's role. The exact
                # weapon-dependent category is provided separately as a scenario.
                preset_id=loadout.get('source_preset_id',loadout['id'])
                components = cost_components(rules, source_id, unit, preset_id)
                scenario = cost_components(rules, source_id, unit, preset_id,
                    mission={'useSpawnTypeByWeaponForSpawnScore': True})
                components['weapon_category_when_mission_flag_enabled'] = scenario['spawn_group']
                components['source'] = source
                components['source_preset_id']=preset_id
                components['primary_armament_id']=loadout.get('primary_armament_id')
                components['primary_armament_sha256']=loadout.get('primary_armament_sha256')
                loadout['spawn_cost_components'] = components
                row.update(status=components['status'], complete_multiplier=components['combined_multiplier'] is not None,
                           weapon_multiplier=components['weapon_multiplier'])
            except ValueError as error: row.update(status='rejected', error=str(error))
            records.append(row)
    report = {'schema_version': '1.0.0', 'source': source, 'records': records,
        'counts': dict(Counter(r['status'] for r in records)),
        'complete_multipliers': sum(r.get('complete_multiplier', False) for r in records),
        'input_catalog_sha256': file_hash(catalog_path), 'generated_final_costs': 0,
        'promoted_spawn_rules': False, 'formal_rankings': 0}
    return catalog, report
