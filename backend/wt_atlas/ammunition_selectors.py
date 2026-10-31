"""Captured client modification-to-bullet selection and ground display rules."""
import hashlib
from pathlib import Path
from .common import content_hash, file_hash
from .forecast.data import number, safe_file
from .native_binding.ordered_blk import parse_ordered_blk

REVIEWED_SCRIPTS = {
    'scripts/weaponry/bulletsinfo.nut': 'c91931778dab0d23ddc8f8719e66ddfd9563dc633c6a1068c6fbee5f4a4398d4',
    'scripts/weaponry/weaponryinfo.nut': '672e6c822cd3688e2f08ae06401720d710877f38466142ccc99ced932ff04249',
    'scripts/unit/baseunittypes.nut': 'd6e02f690ba0aa7f143c602a3d635f29bd9949071111fe7030c83e6868a90f47',
}


def selector_effects(block):
    """getModificationBulletsEffect returns the first relevant ordered parameter."""
    roots = [x for x in block['children'] if x['name'] == 'modifications']
    if len(roots) != 1: raise ValueError('Expected one modification registry')
    result = {}; seen = set()
    for modification in roots[0]['children']:
        name = modification['name']
        if name in seen: raise ValueError('Duplicate global modification identity')
        seen.add(name)
        effects = [x for x in modification['children'] if x['name'] == 'effects']
        if len(effects) > 1: raise ValueError('Ambiguous modification effects')
        for p in effects[0]['params'] if effects else []:
            if p['name'] not in ('bulletMod', 'additiveBulletMod'): continue
            if not isinstance(p['value'], str) or not p['value']:
                raise ValueError('Invalid bullet selection effect')
            result[name] = {'kind': p['name'], 'selector': p['value']}
            break
    return result


def verified_rules(capture, version):
    packs = capture['packs']; scripts = {}
    for name in ('gui.vromfs.bin', 'char.vromfs.bin'):
        pack = packs[name]
        if pack['version'] != version or file_hash(pack['saved']) != pack['sha256']:
            raise ValueError('Ammunition selector capture mismatch')
    for name, expected in REVIEWED_SCRIPTS.items():
        raw = safe_file(Path(packs['gui.vromfs.bin']['extracted']), name).read_bytes()
        lf = hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()
        if lf != expected: raise ValueError('Unreviewed ammunition selector script: ' + name)
        scripts[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'lf_sha256': lf}
    path = safe_file(Path(packs['char.vromfs.bin']['extracted']), 'config/modifications.blk')
    selectors = selector_effects(parse_ordered_blk(path))
    return {'selectors': selectors, 'evidence': {'source_version': version,
        'gui_pack_sha256': packs['gui.vromfs.bin']['sha256'],
        'char_pack_sha256': packs['char.vromfs.bin']['sha256'],
        'modifications_path': 'config/modifications.blk', 'modifications_sha256': file_hash(path),
        'selectors_sha256': content_hash(selectors), 'scripts': scripts,
        'scope': 'ground_mounted_modification_effect_and_named_cannon_first_projectile_label'}}


def named_cannon_label(candidate, localization):
    """A named ground cannon belt uses its first projectile's display label.

    This identifies the entire ordered belt, never makes it homogeneous. The
    15 mm threshold is isCaliberCannon in the reviewed client script.
    """
    if candidate['selector'] == '@default': return None
    if any(m.get('trigger') == 'countermeasures' for m in candidate['mounts']): return None
    bullet = candidate['bullets'][0]
    caliber = bullet.get('rocket', bullet).get('caliber')
    if not number(caliber) or caliber * 1000 < 15: return None
    name = bullet['bulletName']
    # Match the client's precedence: an existing direct translation wins.
    return next((localization.values[k] for k in (name, 'weapons/' + name + '/short')
                 if k in localization.values), None)
