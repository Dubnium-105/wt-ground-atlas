"""Use exact per-projectile client-export points only for isolated research.

The export does not contain a client version or per-selector provenance, so a
matching mounted projectile alone never makes these data publication evidence.
"""
import copy

from ..ammunition_recovery import source_rows
from ..common import content_hash
from .data import number

DISTANCES = (10., 100., 500., 1000.)
ADAPTER = 'unverified-client-export-exact-points-v1'


def exact_export_points(database, unit_id, projectile):
    name = projectile.get('bulletName'); typ = projectile.get('bulletType', '')
    if not isinstance(name, str) or not name or not typ.startswith(('ap', 'sap')):
        raise ValueError('A named kinetic projectile is required')
    if projectile.get('rocket') or projectile.get('cumulativeDamage') or projectile.get('damage', {}).get('kinetic', {}).get('damageType') != 'generic':
        raise ValueError('Unsupported projectile mechanism')
    bullet = database.get('bullets', {}).get(name)
    if not isinstance(bullet, dict) or bullet.get('bulletTypeId') != typ or bullet.get('units') != [unit_id]:
        raise ValueError('Export projectile identity or single-unit scope mismatch')
    raw = bullet.get('raw', {})
    for key in ('mass', 'speed', 'caliber', 'Cx', 'damageCaliber'):
        if key in projectile or key in raw:
            if not number(projectile.get(key)) or not number(raw.get(key)) or projectile[key] != raw[key]:
                raise ValueError('Export raw projectile mismatch: ' + key)
    if any(not number(projectile.get(key)) or projectile[key] <= 0 for key in ('mass', 'speed', 'caliber')):
        raise ValueError('Incomplete physical projectile identity')
    record = bullet.get('penetrationByUnit', {}).get(unit_id, {}).get('kinetic', {})
    ttx = bullet.get('ttxByUnit', {}).get(unit_id, {})
    distances, values = record.get('distances'), record.get('values')
    # The exporter's fallback may be total penetration including a non-kinetic
    # mechanism; only an explicit kinetic table is eligible for this KE model.
    expected_values = ttx.get('armorPiercingKinetic')
    if distances != ttx.get('armorPiercingDist') or values != expected_values:
        raise ValueError('Export penetration and per-unit parameters disagree')
    if not isinstance(distances, list) or not isinstance(values, list) or len(distances) != len(values):
        raise ValueError('Invalid exported exact-point table')
    points = {}
    for distance, angles in zip(distances, values):
        if not number(distance) or not isinstance(angles, dict): raise ValueError('Invalid exported point')
        if distance in points: raise ValueError('Duplicate exported distance')
        value = angles.get('0')
        if not number(value) or value <= 0: raise ValueError('Missing exact zero-angle penetration')
        points[distance] = value
    if not set(DISTANCES).issubset(points): raise ValueError('Four exact requested distances required')
    return {d: points[d] for d in DISTANCES}


def research_profiles(database, unit_id, projectile, response, evidence):
    points = exact_export_points(database, unit_id, projectile)
    if not evidence.get('source_version') or not evidence.get('datamine_commit'):
        raise ValueError('Source geometry version and datamine identity required')
    typ = projectile['bulletType']; caliber = projectile.get('damageCaliber')
    if caliber is None and not typ.startswith(('apds', 'apcr')): caliber = projectile.get('caliber')
    if not number(caliber) or caliber <= 0: raise ValueError('Missing kinetic damage caliber')
    dispersion = response['dispersion']
    if not number(dispersion) or not 0 <= dispersion < 1: raise ValueError('Invalid source dispersion')
    slope = source_rows(response['slope'], projectile.get('slopeEffectPreset'), 'slopeEffect', True)
    ricochet = source_rows(response['ricochet'], projectile.get('ricochetPreset'), 'ricochetProbability')
    profiles = []
    for distance, penetration in points.items():
        profile = {'mode': 'KE', 'reference_only': True, 'penetration_mm': penetration,
            'distance_m': distance, 'normalized_distance_curve': [], 'dispersion': dispersion,
            'damage_caliber_mm': caliber * 1000, 'mechanism_id': 0, 'air_loss_mm_per_m': 0.,
            'slope_rows': slope, 'ricochet_rows': ricochet,
            'provenance': {'adapter': ADAPTER, 'client_version': evidence['source_version'],
                'datamine_commit': evidence['datamine_commit'], 'unit_id': unit_id, 'projectile_name': projectile['bulletName'],
                'source_projectile_sha256': content_hash(projectile), 'evidence': copy.deepcopy(evidence),
                'export_version_verified': False, 'export_selector_verified': False,
                'scope': 'Conditional impact at one exact exported table point; supplied export provenance, firing order, hit and damage remain unverified.'}}
        profile['name'] = 'research_export_point_' + content_hash(profile)[:24]
        profiles.append(profile)
    return profiles
