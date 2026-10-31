"""Read-only review of one client's projectile bulletCount loading default.

The loader default is not a firing-order or projectile-probability model.
"""
import hashlib
from pathlib import Path

from ..common import file_hash, read_json
from ..native_binding.ordered_blk import as_json, parse_ordered_blk
from .data import safe_file

VERSION = '2.59.0.92'
EXE_SHA256 = '17b75d78a20d657b96c9027ac06af211203b94327fbf747813e5367d12e60807'
PACK_SHA256 = 'ef7e4861050d4213b2759f3fb817cc78868f777943afc76608642efe25d7b285'
RANGES = {
    'field_loader': (21080488, 32, '0c29fc8c876c832b3afd66178074d725f48d93261f8b9b6b08b065a9e1dd5e58'),
    'integer_getter': (49372128, 446, 'da1732b57920db45d4bb25ffd26e5720d4185a37b88d8765e5cc40e35ca9e3ec'),
}
VEHICLES = ('jp_btr_3e1', 'ussr_bmd_4m2', 'ussr_btr_82at')


def observed_selectors(unit, weapon):
    """Retain source order and only selectors declared by this exact unit."""
    mods = unit.get('modifications', {})
    rows = []
    for name, block in weapon.items():
        if name not in mods or not isinstance(block, dict) or 'bullet' not in block: continue
        bullets = block['bullet'] if isinstance(block['bullet'], list) else [block['bullet']]
        if any(not isinstance(item, dict) for item in bullets): raise ValueError('Invalid source projectile')
        rows.append({'selector': name, 'source_projectile_blocks': [
            {'name': b.get('bulletName'), 'type': b.get('bulletType'),
             'bulletCount_present': 'bulletCount' in b, 'bulletCount': b.get('bulletCount')}
            for b in bullets]})
    return rows


def verify_instruction_ranges(executable):
    path = Path(executable)
    if path.stat().st_size != 131560160 or file_hash(path) != EXE_SHA256: raise ValueError('Unreviewed client executable')
    hashes = {}
    with path.open('rb') as stream:
        for name, (offset, size, expected) in RANGES.items():
            stream.seek(offset); data = stream.read(size); actual = hashlib.sha256(data).hexdigest()
            if len(data) != size or actual != expected: raise ValueError('Reviewed native range changed: ' + name)
            hashes[name] = {'offset': offset, 'size': size, 'sha256': actual}
    return hashes


def audit(executable, capture_path):
    hashes = verify_instruction_ranges(executable)
    pack = read_json(capture_path)['packs']['aces.vromfs.bin']
    if pack['version'] != VERSION or pack['sha256'] != PACK_SHA256 or file_hash(pack['saved']) != PACK_SHA256:
        raise ValueError('Unreviewed client source pack')
    root = Path(pack['extracted']); sources = {}; rows = []
    def read(path):
        source = safe_file(root, path); before = file_hash(source)
        result = as_json(parse_ordered_blk(source))
        if file_hash(source) != before: raise ValueError('Client source changed during audit')
        sources[path] = before
        return result
    for vehicle in VEHICLES:
        unit_path = 'gamedata/units/tankmodels/' + vehicle + '.blk'; unit = read(unit_path)
        mounts = unit['commonWeapons']['Weapon']; mounts = mounts if isinstance(mounts, list) else [mounts]
        selected = [m for m in mounts if m.get('trigger') == 'gunner0' and '30mm_' in m.get('blk', '').lower()]
        if len(selected) != 1: raise ValueError('Primary autocannon source is ambiguous')
        weapon_path = selected[0]['blk'].replace('\\', '/').lower(); weapon = read(weapon_path)
        rows.append({'vehicle_id': vehicle, 'unit_path': unit_path, 'weapon_path': weapon_path,
                     'trigger': 'gunner0', 'declared_selectors': observed_selectors(unit, weapon)})
    if (file_hash(pack['saved']) != PACK_SHA256 or file_hash(executable) != EXE_SHA256 or
            any(file_hash(safe_file(root, path)) != sha for path, sha in sources.items())):
        raise ValueError('Client evidence changed during audit')
    return {'schema_version': '1.0.0', 'source_version': VERSION,
        'executable_sha256': EXE_SHA256, 'client_pack_sha256': PACK_SHA256,
        'capture_sha256': file_hash(capture_path), 'native_ranges': hashes, 'source_sha256': sources,
        'native_observation': 'The reviewed field loader passes an int32 zero default to the integer getter for bulletCount; the getter returns the supplied default on its missing/unreadable path.',
        'missing_field_read_default': 0, 'source_block_order_is_firing_order': 'unverified',
        'bullet_count_runtime_meaning': 'unverified', 'live_client_execution': False,
        'vehicles': rows, 'publishable': False, 'formal_rankings': 0}
