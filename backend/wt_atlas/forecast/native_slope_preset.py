"""Replay a narrow, static review of the 2.59.0.92 native slope-preset loader.

This evidence does not identify the runtime behavior of the sentinel index and
must not be used as a projectile slope table or a production physics profile.
"""
import hashlib
from pathlib import Path

from ..ammunition_recovery import Localization, MountedAmmunition
from ..ammunition_selectors import verified_rules as selector_rules
from ..common import content_hash, file_hash, read_json
from ..default_belts import verified_rules as default_rules
from .data import safe_file

VERSION = '2.59.0.92'
EXE_SHA256 = '17b75d78a20d657b96c9027ac06af211203b94327fbf747813e5367d12e60807'
EXE_SIZE = 131560160
PACK_SHA256 = 'ef7e4861050d4213b2759f3fb817cc78868f777943afc76608642efe25d7b285'
RANGES = {
    'fresh_object_initialization_a': (15556117, 31, 'b4a824267c6a68896506a25b9e30fa063df4e9d23e6075cc2a67f34ed210530c'),
    'fresh_object_initialization_b': (21146651, 31, 'b4a824267c6a68896506a25b9e30fa063df4e9d23e6075cc2a67f34ed210530c'),
    'null_default_lookup_and_branch': (71910207, 38, '4992782934a3b49cfdf2e0cc46417d6db6e67a09d824ca2b78139951414906c5'),
    'string_getter': (49369968, 442, '9d9442ded054dc70a63a3f97fc455b4fa9ba503a3cf0de0f3d6e2d275b32d02d'),
    'resolved_index_store': (71910519, 14, '750b751fc476cf2bdc1006bfc25dc2920021c9aaaa5607110e85d38e37000fd2'),
}


def verify_instruction_ranges(executable):
    path = Path(executable)
    if path.stat().st_size != EXE_SIZE or file_hash(path) != EXE_SHA256:
        raise ValueError('Unreviewed client executable')
    result = {}
    with path.open('rb') as stream:
        for name, (offset, size, expected) in RANGES.items():
            stream.seek(offset)
            data = stream.read(size)
            digest = hashlib.sha256(data).hexdigest()
            if len(data) != size or digest != expected:
                raise ValueError('Reviewed native range changed: ' + name)
            result[name] = {'offset': offset, 'size': size, 'sha256': digest}
    return result


def projectile_evidence(binding):
    bullets = binding.get('bullets', [])
    if (binding.get('unit_path') != 'gamedata/units/tankmodels/ussr_t_60_1941.blk' or
            binding.get('weapon_path') != 'gamedata/weapons/groundmodels_weapons/20mm_tnsh_user_cannon.blk' or
            binding.get('selector') != '20mm_ussr_TNSh_AP' or len(bullets) != 1):
        raise ValueError('Unexpected T-60 mounted selection')
    bullet = bullets[0]
    if (bullet.get('bulletName') != '20mm_shvak_apit' or
            bullet.get('bulletType') != 'ap_i' or 'slopeEffectPreset' in bullet):
        raise ValueError('Reviewed projectile or its missing field changed')
    return {'bullet_name': bullet['bulletName'], 'bullet_type': bullet['bulletType'],
            'projectile_sha256': content_hash(bullet), 'slope_effect_preset_present': False,
            'ricochet_preset': bullet.get('ricochetPreset')}


def audit(executable, capture_path, language_path, catalog_path):
    identities = {str(p): file_hash(p) for p in (capture_path, language_path, catalog_path)}
    ranges = verify_instruction_ranges(executable)
    capture, language, catalog = (read_json(p) for p in (capture_path, language_path, catalog_path))
    pack = capture['packs']['aces.vromfs.bin']
    if (pack['version'] != VERSION or pack['sha256'] != PACK_SHA256 or file_hash(pack['saved']) != PACK_SHA256 or
            language['version'] != VERSION or file_hash(language['saved']) != language['source_sha256'] or
            catalog['snapshot']['client_source']['version'] != VERSION or
            catalog['snapshot']['datamine_game_version'] != VERSION):
        raise ValueError('Client, localization, or catalog identity changed')
    language_root = Path(language['extracted']) / 'lang'
    localization = Localization(language_root, include_defaults=True)
    default_evidence = default_rules(capture, VERSION)
    selection_evidence = selector_rules(capture, VERSION)
    adapter = MountedAmmunition(pack['extracted'], localization,
        default_rules=default_evidence, selector_rules=selection_evidence,
        structural_projectiles=True)
    vehicles = [v for v in catalog['vehicles'] if v['id'] == 'ussr_t_60_1941']
    if len(vehicles) != 1: raise ValueError('T-60 catalog identity is ambiguous')
    vehicle = vehicles[0]
    selections = [a for a in vehicle.get('ammo', []) if a.get('id') == 'ussr_t_60_1941::20 mm API']
    if len(selections) != 1: raise ValueError('T-60 ammunition selection is ambiguous')
    binding = adapter.bind(adapter.candidates(vehicle), selections[0])
    projectile = projectile_evidence(binding)
    source_hashes = dict(adapter.sources)
    root = Path(__file__).resolve().parents[3]
    code_paths = (Path(__file__).resolve(), root / 'scripts/audit_native_slope_preset.py')
    code_sha = {str(path.relative_to(root)).replace('\\', '/'): hashlib.sha256(
        path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() for path in code_paths}
    if (file_hash(executable) != EXE_SHA256 or file_hash(pack['saved']) != PACK_SHA256 or
            file_hash(language['saved']) != language['source_sha256'] or
            any(file_hash(safe_file(Path(pack['extracted']), path)) != sha for path, sha in source_hashes.items()) or
            any(file_hash(safe_file(language_root, path)) != sha for path, sha in localization.sources.items()) or
            default_rules(capture, VERSION) != default_evidence or selector_rules(capture, VERSION) != selection_evidence or
            any(file_hash(Path(path)) != sha for path, sha in identities.items())):
        raise ValueError('Reviewed evidence changed during audit')
    return {'schema_version': '1.0.0', 'source_version': VERSION,
        'executable_sha256': EXE_SHA256, 'client_pack_sha256': PACK_SHA256,
        'capture_sha256': identities[str(capture_path)], 'language_capture_sha256': identities[str(language_path)],
        'catalog_sha256': identities[str(catalog_path)], 'source_sha256': source_hashes, 'code_sha256_lf': code_sha,
        'localization_sha256': localization.sources, 'default_belt_rules': default_evidence,
        'selector_rules': selection_evidence['evidence'],
        'native_ranges': ranges, 'vehicle_id': vehicle['id'], 'ammo_id': selections[0]['id'],
        'binding': {k: v for k, v in binding.items() if k != 'bullets'}, 'projectile': projectile,
        'observation': 'In two reviewed fresh-object paths, the field later written by the resolved slope-preset lookup is initialized to -1. The lookup supplies a null default; the getter returns it on missing/unreadable paths, and the caller skips its index write when the result is null.',
        'limits': 'Static native paths do not establish inherited input, reused-object state, or the sentinel’s runtime angular response.',
        'slope_response_verified': False, 'physics_profile_created': False,
        'live_client_execution': False, 'formal_rankings': 0, 'publishable': False}
