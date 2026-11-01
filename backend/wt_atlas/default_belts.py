"""Reviewed client rules for the ground-vehicle default ammunition label."""
import hashlib
from pathlib import Path

from .common import file_hash
from .forecast.data import number, safe_file


# The port covers bulletsInfo's belt predicate and default label, the mounted
# weapon basename/localization rule, and the ground unit's multiple-belt flag.
# A different script requires review before it can supply ownership evidence.
REVIEWED_SCRIPTS = {
    'scripts/weaponry/bulletsinfo.nut': 'c91931778dab0d23ddc8f8719e66ddfd9563dc633c6a1068c6fbee5f4a4398d4',
    'scripts/weaponry/weaponryinfo.nut': '672e6c822cd3688e2f08ae06401720d710877f38466142ccc99ced932ff04249',
    'scripts/weaponry/weaponrydescription.nut': 'bc0a3e010f6c07112618774d369f8cc997204bdf8c39b7820536713721405629',
    'scripts/unit/baseunittypes.nut': 'd6e02f690ba0aa7f143c602a3d635f29bd9949071111fe7030c83e6868a90f47',
}


def verified_rules(capture, version):
    pack = capture.get('packs', {}).get('gui.vromfs.bin', {})
    if pack.get('version') != version:
        raise ValueError('Default-belt GUI capture version mismatch')
    if file_hash(pack['saved']) != pack['sha256']:
        raise ValueError('Default-belt GUI pack checksum mismatch')
    scripts = {}
    for name, expected in REVIEWED_SCRIPTS.items():
        raw = safe_file(Path(pack['extracted']), name).read_bytes()
        normalized = hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()
        if normalized != expected:
            raise ValueError('Default-belt GUI script is not reviewed: ' + name)
        scripts[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'lf_sha256': normalized}
    return {'source_version': version, 'gui_pack_sha256': pack['sha256'],
            'scripts': scripts, 'scope': 'ground_default_belt_and_exact_parent_weapon'}


def candidate_fields(weapon, mounts):
    cartridge = weapon.get('bulletsCartridge', 0)
    is_belt = ((weapon.get('isBulletBelt') is not False or
                (number(cartridge) and cartridge > 1)) and
               not weapon.get('useSingleIconForBullet', False))
    usable = mounts and all(not m.get('dummy') and m.get('trigger') != 'countermeasures' for m in mounts)
    keys = sorted({'weapons/' + m['blk'].replace('\\', '/').rsplit('/', 1)[-1].removesuffix('.blk') for m in mounts})
    return {'default_belt_eligible': bool(is_belt and usable and not weapon.get('notUseDefaultBulletInGui')),
            'weapon_localization_keys': keys, 'mounted_weapon_count': len(mounts)}


def match_default(candidate, ammo, localization):
    if candidate['selector'] != '@default' or not candidate.get('default_belt_eligible'):
        return []
    label = localization.match(['modification/default_bullets'], ammo['name'])
    if not label:
        return []
    parent = ammo.get('provenance', {}).get('parent_weapon_name')
    count = candidate['mounted_weapon_count']; weapon_names = []
    for key in candidate['weapon_localization_keys']:
        loc = localization.values.get(key)
        if loc and parent == (f'{count} x ' if count > 1 else '') + loc['value']:
            weapon_names.append(loc)
    # "Default" alone cannot identify which mounted weapon supplied the Wiki
    # table. Its parent title and the number of physical mounts must also match.
    return label + weapon_names if weapon_names else []
