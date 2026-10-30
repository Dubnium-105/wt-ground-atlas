"""Portable client discovery; explicit paths and WT_CLIENT_ROOT take precedence."""
import os
from pathlib import Path


def candidates():
    paths=[]
    if os.environ.get('WT_CLIENT_ROOT'):paths.append(Path(os.environ['WT_CLIENT_ROOT']))
    home=Path.home()
    paths.extend([home/'.steam/steam/steamapps/common/War Thunder',home/'.local/share/Steam/steamapps/common/War Thunder'])
    for variable in ('ProgramFiles(x86)','ProgramFiles'):
        if os.environ.get(variable):paths.append(Path(os.environ[variable])/'Steam/steamapps/common/War Thunder')
    if os.name=='nt':
        for drive in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
            paths.extend(Path(drive+':/')/p/'steamapps/common/War Thunder' for p in ('Steam','SteamLibrary'))
    return sorted({p.resolve() for p in paths if p.is_dir()})


def find_client():
    found=candidates()
    if os.environ.get('WT_CLIENT_ROOT') and Path(os.environ['WT_CLIENT_ROOT']).is_dir():return os.environ['WT_CLIENT_ROOT']
    if len(found)==1:return str(found[0])
    raise ValueError('Set --source or WT_CLIENT_ROOT to one installed War Thunder directory; found '+str(len(found)))
