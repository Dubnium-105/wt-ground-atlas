"""Exact source-block identity when a native projectile has no display ID.

No name is inserted into the raw projectile. A content hash, weapon source,
selection and ordered block position identify it without caliber/name matching.
"""
from .common import content_hash


def identities(weapon_path, weapon_sha256, selector, bullets):
    result = []
    for index, bullet in enumerate(bullets):
        if not isinstance(bullet, dict):
            raise ValueError('Invalid projectile source block')
        name = bullet.get('bulletName')
        if 'bulletName' in bullet and (not isinstance(name, str) or not name):
            raise ValueError('Ambiguous declared projectile identity')
        source = {'schema_version': '1.0.0', 'kind': 'exact_source_projectile_block',
            'source_weapon_path': weapon_path, 'source_weapon_sha256': weapon_sha256,
            'selector': selector, 'bullet_index': index,
            'projectile_sha256': content_hash(bullet), 'declared_bullet_name': name}
        result.append({**source, 'id': 'source-projectile:' + content_hash(source)})
    return result
