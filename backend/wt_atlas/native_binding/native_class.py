"""Pinned full class-property loading (0x1433a8350), before part state.

The scalar SSE cosine path is modeled with a double cos rounded to float32;
its agreement is checked on the actual configured angles and bounded cases.
Runtime CPU dispatch and full part-template expansion are separate inputs.
"""
from copy import deepcopy
import math
import numpy as np
from .native_material import default_armor_block,load_armor_block,load_mechanism_array,typed_parameter

f=np.float32
FLAG_FIELDS={
    'oneSided':(0,False),'externalArmor':(3,False),'createSecondaryShatters':(4,True),
    'allowRicochet':(5,True),'detachable':(6,False),'disableProjectleBreaking':(7,False),
    'useForSeriousHit':(8,True),'checkFuseRelDamageThreshold':(9,True),'simpleRicochet':(10,False),
    'ldmInner':(11,False),'embedded':(12,False),'reactiveArmor':(13,False),'ignoreDeadWithSplash':(14,False),
    'castOnKillAfterTankPartDead':(15,False),'variableThickness':(16,False),'castOnHitOfSelfDamage':(17,True),
    'disableShatterStopChanceOnDeadPart':(18,False)}
CLASS_SCALARS={'stopChanceOnDeadPart':(0x10,0.),'splashPenetrationToArmorThreshold':(0x14,0.),
    'restrainExplosionDamage':(0x18,1.),'materialDensity':(0x1c,7700.),'dmgState':(0x20,-1.),
    'responseImpulse':(0x24,0.),'slopeMinCaliber':(0x28,0.),'kineticPenetrationToArmorThreshold':(0x2c,-1.),
    'solidArmorTestDepth':(0x30,-1.)}


def default_class_block():
    return {'flags':0,'scalars':{name:float(f(value)) for name,(_,value) in CLASS_SCALARS.items()},
        'ricochet_angle_enabled':False,'ricochet_cosine':0.,'ricochetCosinePower':1.,
        'armor':default_armor_block(),'damage_mult':{'values':[],'status':[]}}


def load_class_block(parameters,names,parents,previous=None):
    state=deepcopy(default_class_block() if previous is None else previous)
    def get(name,kind,default):return typed_parameter(parameters,name,kind,default)
    flags=state['flags']
    for name,(bit,inverted) in FLAG_FIELDS.items():
        default=bool(flags&(1<<bit))^inverted
        value=bool(get(name,'b',default))^inverted
        flags=(flags&~(1<<bit))|(int(value)<<bit)
    hit_type=get('hitTestType','t',None)
    if hit_type is not None:
        flags=(flags&~6)|(2 if hit_type=='armor' else 4 if hit_type=='inner' else 0)
    state['flags']=flags
    for name in CLASS_SCALARS:state['scalars'][name]=float(f(get(name,'r',state['scalars'][name])))
    angle=f(get('ricochetAngle','r',0.))
    if angle>f(0.):
        state['ricochet_angle_enabled']=True
        state['ricochet_cosine']=float(f(math.cos(float(f(angle*f(0.01745329238474369))))))
        state['ricochetCosinePower']=float(f(get('ricochetCosinePower','r',state['ricochetCosinePower'])))
    state['armor']=load_armor_block(parameters,names,parents,state['armor'])
    old=state['damage_mult']
    state['damage_mult']=load_mechanism_array(parameters,names,parents,1.,suffix='DamageMult',initial_values=old['values'],initial_status=old['status'])
    return state


def decoded_class_flags(flags):
    return {**{name:bool(flags&(1<<bit))^inverted for name,(bit,inverted) in FLAG_FIELDS.items()},
        'hitTestType':'armor' if flags&2 else 'inner' if flags&4 else None}
