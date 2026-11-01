"""Conditional system-member shot bounds for the lineup forecast."""
from .data import digest,number
from .system_physics import declared_members


def system_shot(engine, attacker, target, scenario, skill, *, projectile_index=0):
    """Retain arbitrary member selection as an interval, never a mean.

    This is a protected-member penetration event, not system disablement. The
    unknown distribution can vary with the attacker's skill and each shot.
    """
    vehicle=target['vehicle'];system_id=vehicle.get('system_id')
    if vehicle.get('system_role')!='basic' or system_id!=target['vehicle_id']:
        raise ValueError('System shot requires the declared basic vehicle')
    if vehicle.get('forecast_physics_invalidated') or attacker['vehicle'].get('forecast_physics_invalidated'):
        return None
    members=declared_members(engine.catalog,system_id);rows=[]
    for member in members:
        member_vehicle=engine.atlas.catalog.get(member['vehicle_id'])
        if not member_vehicle:raise ValueError('System member absent from physical catalog')
        variant={**target,'vehicle_id':member['vehicle_id'],'vehicle':member_vehicle}
        shot=engine._single_shot(attacker,variant,scenario,skill,projectile_index=projectile_index)
        if shot and shot.get('source_binding')!='independent_geometry_verified':shot=None
        if shot and (not number(shot.get('lower')) or not number(shot.get('upper')) or not 0<=shot['lower']<=shot['upper']<=1):
            raise ValueError('Invalid system member shot interval')
        rows.append({**member,'status':'source_bound_shot' if shot else 'missing_physical_shot',
            'lower':shot['lower'] if shot else 0.,'upper':shot['upper'] if shot else 1.,
            'shot_key':shot['aim_cache_key'] if shot else None})
    lower=min(row['lower'] for row in rows);upper=max(row['upper'] for row in rows)
    identity={'system_id':system_id,'attacker_key':attacker['key'],'scenario_id':scenario['id'],
              'skill':skill,'projectile_index':projectile_index,'members':rows}
    key=digest(identity)
    result={'mean':None,'lower':lower,'upper':upper,'unknown_mass':1.,'lethality':None,
        'aim_cache_key':key,'system_target_selection_unmodeled':True,'members':rows,
        'interpretation':'Conditional protected-member penetration; target selection and system disablement are unmodeled.'}
    engine.cache.evidence_put(key,'system_shot',{**identity,**result})
    return result
