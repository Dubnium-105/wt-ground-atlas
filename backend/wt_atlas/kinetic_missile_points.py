"""Exact source-table points for accelerating kinetic missiles, for research only.

These profiles deliberately bypass range interpolation: a rocket's acceleration
is not the monotone velocity-loss curve assumed by the supplied V2 adapter.
They cannot enter Atlas ranking, which rejects reference_only profiles.
"""
import copy
import math

from .ammunition_recovery import source_rows
from .common import content_hash
from .forecast.data import number

DISTANCES = (10., 100., 500., 1000.)
ADAPTER = 'kinetic-missile-exact-table-points-v1'


def same_number(value):
    values = value if isinstance(value, list) else [value]
    if not values or any(not number(x) or x != values[0] for x in values):
        raise ValueError('Conflicting or missing native scalar')
    return values[0]


def profiles(ammo, binding, table, wiki, response, version, commit):
    bullets = binding.get('bullets', [])
    if len(bullets) != 1 or binding.get('is_belt') is not False:
        raise ValueError('Exact single mounted projectile required')
    bullet = bullets[0]
    if not isinstance(bullet, dict):
        raise ValueError('Invalid native projectile')
    rocket = bullet.get('rocket')
    if (bullet.get('bulletType') != 'atgm_ke_tank' or not isinstance(rocket, dict) or
        rocket.get('bulletType') != 'atgm_ke_tank' or rocket.get('penetrationBySpeed') is not True or
        rocket.get('damage', {}).get('kinetic', {}).get('damageType') != 'generic' or
        bullet.get('cumulativeDamage') or rocket.get('cumulativeDamage')):
        raise ValueError('Unsupported native kinetic missile')
    caliber = same_number(rocket.get('damageCaliber'))
    if caliber <= 0 or same_number(bullet.get('damageCaliber')) != caliber:
        raise ValueError('Native projectile caliber mismatch')
    if (not number(ammo.get('caliber_mm')) or not number(rocket.get('caliber')) or
        not math.isclose(ammo['caliber_mm'], rocket['caliber'] * 1000, abs_tol=.01)):
        raise ValueError('Catalog rocket caliber mismatch')
    if not any(x.get('key') == bullet.get('bulletName') and x.get('value') == ammo.get('name')
               for x in binding.get('localization', [])):
        raise ValueError('Exact projectile localization required')
    if not table or table.get('points') != ammo.get('penetration') or table.get('maximum_only'):
        raise ValueError('Exact non-maximum-only Wiki table required')
    points = {}
    for point in table['points']:
        if point['angle_deg'] != 0 or point['distance_m'] not in DISTANCES:
            continue
        distance, penetration = point['distance_m'], point['mm']
        if distance in points or not number(penetration) or penetration <= 0:
            raise ValueError('Invalid or ambiguous exact source point')
        points[distance] = penetration
    if set(points) != set(DISTANCES):
        raise ValueError('Four exact source-distance points required')
    minimum, maximum = rocket.get('minDistance'), rocket.get('maxDistance')
    if not number(minimum) or not number(maximum) or not 0 <= minimum < maximum:
        raise ValueError('Native missile range parameters missing')
    dispersion = response['dispersion']
    if not number(dispersion) or not 0 <= dispersion < 1:
        raise ValueError('Invalid native dispersion')
    slope = source_rows(response['slope'], rocket.get('slopeEffectPreset'), 'slopeEffect', True)
    ricochet = source_rows(response['ricochet'], rocket.get('ricochetPreset'), 'ricochetProbability')
    proof = {k: copy.deepcopy(v) for k, v in binding.items() if k != 'bullets'}
    proof.update(projectile_sha256=content_hash(bullet), rocket_sha256=content_hash(rocket), wiki=wiki,
                 exact_table_sha256=content_hash(table), catalog_ammo_id=ammo['id'])
    output = []
    for distance in DISTANCES:
        profile = {'mode': 'KE', 'reference_only': True, 'penetration_mm': points[distance], 'distance_m': distance,
                   'normalized_distance_curve': [], 'dispersion': dispersion, 'damage_caliber_mm': caliber * 1000,
                   'mechanism_id': 0, 'air_loss_mm_per_m': 0., 'slope_rows': slope, 'ricochet_rows': ricochet,
                   'provenance': {'client_version': version, 'datamine_commit': commit,
                       'penetration_source_ammo_ids': [ammo['id']], 'adapter': ADAPTER,
                       'source_weapon_path': binding['weapon_path'], 'source_weapon_sha256': binding['weapon_sha256'],
                       'source_hashes': response['hashes'], 'binding': proof,
                       'source_min_distance_m': minimum, 'source_max_distance_m': maximum,
                       'inside_declared_distance_bounds': minimum <= distance <= maximum,
                       'scope': 'Conditional impact at this exact source-table distance; flight, firing eligibility, activation and hit probability are not modeled. No interpolation or monotone decay is inferred.'}}
        profile['name'] = 'research_kinetic_point_' + content_hash(profile)[:24]
        output.append(profile)
    return output
