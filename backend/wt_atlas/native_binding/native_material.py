"""Pinned armor-class resolution before the property-loading callback.

0x1433278a0. Ordered typed parameters and name IDs are explicit input.
Runtime name-pool IDs and the per-vehicle loader configuration are not inferred.
"""
from __future__ import annotations
from copy import deepcopy
import numpy as np
f=np.float32
ARMOR_SCALARS={'armorThickness':4,'minSolidArmorThickness':8,'armorSplashThicknessFloor':0xc,
    'armorSplashThicknessMapVoxelSize':0x10,'armorEffectiveThicknessMax':0x14,'armorThrough':0x18,
    'armorQuality':0x1c,'restrainDamage':0x80,'ricochetDamage':0x84}
ARMOR_ARRAYS={'ArmorQuality':(0x20,1.),'EffectiveThicknessMax':(0x40,None),'ThicknessEquivalentMax':(0x60,2147440000.)}


def typed_parameter(parameters,name,kind,default=None):
    """Native named getters inspect the first name match, then its type."""
    parameter=next((p for p in parameters if p['name']==name),None)
    return parameter['value'] if parameter is not None and parameter['type']==kind else default


def register_mechanisms(root):
    """0x143696f00 on a fresh descriptor: ordered names and parent links.

    Unknown parent names map to 65535/None; cycles are broken in source order.
    The loader does not topologically sort the mechanism names.
    """
    names=[child['name'] for child in root['children']];parents=[];broken=[]
    for child in root['children']:
        name=typed_parameter(child['params'],'inherit','t')
        parents.append(names.index(name) if name and name in names else None)
    for index in range(len(parents)):
        ancestor=index
        for _ in range(len(parents)+1):
            ancestor=parents[ancestor]
            if ancestor is None:break
        else:
            parents[index]=None;broken.append(index)
    return {'names':names,'parents':parents,'broken_cycle_indices':broken}


def resolve_damageparts_tree(root,classes,name_ids,*,allowed_names=None):
    """Pristine format-2 ordinary branch of 0x1433286a0.

    Returns ordered callback inputs for each accepted leaf. The nearest
    ancestor (including the leaf) declaring armorClass supplies class lookup;
    all ancestor/leaf blocks still supply their own direct scalar fields.
    Runtime modifications, prefix stripping and pre-expanded part defaults
    are explicit separate inputs, not reconstructed by this helper.
    """
    leaves=[]
    def walk(block,ancestors,class_at):
        if classes is not None and any(p['name']=='armorClass' for p in block['params']):class_at=len(ancestors)
        if block['children']:
            for child in block['children']:walk(child,ancestors+[block],class_at)
            return
        if allowed_names is not None and block['name'] not in allowed_names:return
        index=len(leaves);callbacks=[]
        for number,source in enumerate(ancestors+[block]):
            value=typed_parameter(source['params'],'armorClass','t');part={} if value is None else {'armorClass':value}
            for item in resolve_class_callbacks(part,classes if number==class_at else None,name_ids,part_index=index):
                if item['block']=='@part':item['block']=source['path']
                callbacks.append(item)
        leaves.append({'name':block['name'],'path':block['path'],'part_index':index,'callbacks':callbacks})
    walk(root,[],0)
    return leaves


def default_armor_block():
    values={name:0. for name in ARMOR_SCALARS}
    values.update(armorEffectiveThicknessMax=float(f(2147440000.)),armorQuality=1.,
        enabled=False,ignoreSolidDimension=False,legacyShatterDamage=False,
        arrays={name:{'values':[],'status':[]} for name in ARMOR_ARRAYS})
    return values


def load_armor_block(parameters,names,parents,previous=None):
    """0x1433a8970, physics fields, typed real/bool getter input contract.

    Scalar fields are read once per overlay. An existing mechanism array is
    preserved on resize-to-same-size; changing its default does not refill it.
    Zero/negative nominal thickness skips all remaining armor-field updates.
    """
    state=deepcopy(previous if previous is not None else default_armor_block())
    def get(name,kind,default):return typed_parameter(parameters,name,kind,default)
    state['armorThickness']=float(f(get('armorThickness','r',state['armorThickness'])))
    if f(state['armorThickness'])<=f(0.):return state
    state['enabled']=True
    for name in ARMOR_SCALARS:
        if name!='armorThickness':state[name]=float(f(get(name,'r',state[name])))
    for name in ['ignoreSolidDimension','legacyShatterDamage']:state[name]=bool(get(name,'b',state[name]))
    for suffix,(_,default) in ARMOR_ARRAYS.items():
        if default is None:default=state['armorEffectiveThicknessMax']
        old=state['arrays'][suffix]
        state['arrays'][suffix]=load_mechanism_array(parameters,names,parents,default,suffix=suffix,
            initial_values=old['values'],initial_status=old['status'])
    return state


def load_mechanism_array(parameters,names,parents,default,*,prefix=None,suffix=None,initial_values=(),initial_status=()):
    """0x1433a8db0: typed fields plus nearest explicitly set ancestor.

    Status 2 is explicit, 1 inherited, 0 default. Input field order and types
    are preserved. The native name comparison permits a mechanism-name prefix;
    this function retains that behavior for malformed/abbreviated fields too.
    """
    count=len(names)
    if not count:return {'values':[float(f(x)) for x in initial_values],'status':list(initial_status)}
    values=[f(x) for x in initial_values[:count]]+[f(default)]*max(0,count-len(initial_values))
    status=list(initial_status[:count])+[0]*max(0,count-len(initial_status))
    prefix=prefix or '';suffix=suffix or ''
    for parameter in parameters:
        name=parameter['name']
        if not name.startswith(prefix) or not name.endswith(suffix) or len(name)<len(prefix)+len(suffix):continue
        middle=name[len(prefix):len(name)-len(suffix) if suffix else None]
        index=next((i for i,mechanism in enumerate(names) if mechanism.startswith(middle)),None)
        if index is None:continue
        status[index]=2;values[index]=f(parameter['value']) if parameter['type']=='r' else f(0.)
    for index in range(len(parents)):
        if status[index]==2:continue
        ancestor=index
        for _ in range(count):
            ancestor=parents[ancestor] if ancestor<len(parents) else None
            if ancestor is None or ancestor==65535:break
            if status[ancestor]==2:
                status[index]=1;values[index]=values[ancestor];break
    return {'values':[float(v) for v in values],'status':status}


def resolve_class_callbacks(part, classes, name_ids, *, part_index=0):
    """Return native callback order and two-material alternate probability.

    Each class is {name, params:[{name,type,value},...]}. Class and parameter
    order are preserved. Type 'r' is a BLK real (native type tag 3).
    """
    result=[];class_id=-1
    class_name=part.get('armorClass') if classes is not None else None
    selected=next((c for c in classes or [] if c['name']==class_name),None)
    if selected is not None and selected['params']:
        class_id=name_ids[class_name];primary=selected;alternate=None;probability=f(0.)
        parameters=selected['params']
        if len(parameters)==2 and all(p['type']=='r' for p in parameters):
            a=next((c for c in classes if c['name']==parameters[0]['name']),None)
            b=next((c for c in classes if c['name']==parameters[1]['name']),None)
            if a is not None and b is not None:
                wa=f(parameters[0]['value']);wb=f(parameters[1]['value']);total=f(wb+wa)
                if total>=f(3.99999987e-19):
                    if abs(f(f(1.)-total))>=f(1.1920928955078125e-7):
                        reciprocal=f(f(1.)/total);wa=f(wa*reciprocal);wb=f(wb*reciprocal)
                    primary=b;alternate=a;probability=wa
                    if wa>=f(.5):primary=a;alternate=b;probability=wb
        result.append({'block':primary['name'],'part_index':part_index,'class_name_id':-1,
            'alternate_block':None if alternate is None else alternate['name'],
            'alternate_probability':float(probability)})
    result.append({'block':'@part','part_index':part_index,'class_name_id':class_id,
        'alternate_block':None,'alternate_probability':0.})
    return result
