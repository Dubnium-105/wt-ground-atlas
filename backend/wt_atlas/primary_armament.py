"""Captured-client default primary armament and explicit empty secondary presets."""
import hashlib
from pathlib import Path
from .common import file_hash
from .forecast.data import safe_file


REVIEWED_SCRIPTS={
    'scripts/weaponry/weaponryinfo.nut':'672e6c822cd3688e2f08ae06401720d710877f38466142ccc99ced932ff04249',
    'scripts/weaponry/weaponrypresets.nut':'64a41b6cf51abb65800691d32be2c175db30063c77d55ccbe64a6a2efb79bb79',
    'scripts/weaponry/bulletsinfo.nut':'c91931778dab0d23ddc8f8719e66ddfd9563dc633c6a1068c6fbee5f4a4398d4',
}


def verified_rules(capture,version):
    pack=capture.get('packs',{}).get('gui.vromfs.bin',{})
    if pack.get('version')!=version or file_hash(pack['saved'])!=pack['sha256']:
        raise ValueError('Primary armament GUI capture mismatch')
    scripts={}
    for name,expected in REVIEWED_SCRIPTS.items():
        raw=safe_file(Path(pack['extracted']),name).read_bytes()
        lf=hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()
        if lf!=expected:raise ValueError('Unreviewed primary armament script: '+name)
        scripts[name]={'sha256':hashlib.sha256(raw).hexdigest(),'lf_sha256':lf}
    return {'source_version':version,'gui_pack_sha256':pack['sha256'],'scripts':scripts,
            'scope':'getCommonWeapons_default_primary_and_additive_fixed_preset'}


def array(value):
    return value if isinstance(value,list) else ([] if value is None else [value])


def weapon_summary(weapons):
    return [{k:row[k] for k in ('id','name','type','count','ammunition_count','ammunition_selection_status','ammunition_quantity_status') if k in row} for row in weapons]


def default_declarations(model):
    """Port getCommonWeapons(..., primaryMod="") and getWeaponsByTypes.

    Primary modifications replace commonWeapons and are separate selections.
    This function deliberately resolves the explicit default selection only.
    """
    common=model.get('commonWeapons')
    if common is None:return [],[]
    if not isinstance(common,dict):raise ValueError('Invalid common weapons block')
    slots={s['index']:s for s in array(model.get('WeaponSlots',{}).get('WeaponSlot'))}
    declarations=[];ignored=[]
    for i,selection in enumerate(array(common.get('Weapon'))):
        if not isinstance(selection,dict):raise ValueError('Invalid common weapon declaration')
        if slots:
            slot=slots.get(selection.get('slot'))
            preset=next((p for p in array((slot or {}).get('WeaponPreset')) if p['name']==selection.get('preset')),None)
            if preset is None:raise ValueError('Default primary mount preset missing')
            weapons=array(preset.get('Weapon'))
        else:weapons=[selection]
        for j,weapon in enumerate(weapons):
            if not isinstance(weapon,dict):raise ValueError('Invalid fixed weapon declaration')
            if not slots and any(row['weapon']==weapon for row in declarations+ignored):continue
            row={'weapon':weapon,'block':'commonWeapons','selection_index':i,'source_weapon_index':j,
                 'required_modification_ids':array(preset.get('reqModification')) if slots else []}
            (ignored if weapon.get('dummy') else declarations).append(row)
    if not slots:
        for i,slot in enumerate(array(model.get('WeaponPilons',{}).get('WeaponSlot'))):
            preset=slot.get('WeaponPreset',{})
            if not isinstance(preset,dict):raise ValueError('Unsupported legacy weapon pylon')
            for j,weapon in enumerate(array(preset.get('Weapon'))):
                # Native appendOnce prevents the same exact declaration being
                # counted again through the legacy pylon block.
                if any(row['weapon']==weapon for row in declarations+ignored):continue
                row={'weapon':weapon,'block':'WeaponPilons','selection_index':i,'source_weapon_index':j}
                (ignored if weapon.get('dummy') else declarations).append(row)
    return declarations,ignored


EMPTY_PRESET_DIRECTIVES={'hideNodes','HideNodes','hideDmParts','HideDmParts',
                        'smokeScreenCount','smokeScreenTime','smokeScreenCooldown'}


def is_empty_preset(data):
    if not isinstance(data,dict):raise ValueError('Invalid preset resource')
    if array(data.get('Weapon')):return False
    if set(data)-EMPTY_PRESET_DIRECTIVES-{'Weapon'}:
        raise ValueError('Unsupported empty preset directives')
    if 'Weapon' in data and data['Weapon']!=[]:
        raise ValueError('Invalid empty preset weapon declaration')
    return True
