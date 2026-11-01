#!/usr/bin/env python3
"""Run or resume every supported system-member research candidate offline."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.system_batch_runner import run


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','assembly','atlas','catalog','ammunition','config','bindings','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--memory-budget',type=int,default=512,help='MiB for decoded map cache and one map load')
    p.add_argument('--aim-entries',type=int,default=50000);p.add_argument('--limit',type=int)
    p.add_argument('--resume',action='store_true');p.add_argument('--keep-awake',action='store_true')
    a=p.parse_args();kernel=None
    if a.keep_awake and sys.platform=='win32':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.SetThreadExecutionState.argtypes=[ctypes.c_ulong];kernel.SetThreadExecutionState.restype=ctypes.c_ulong
        if not kernel.SetThreadExecutionState(0x80000001):raise OSError(ctypes.get_last_error(),'Cannot keep system awake')
    try:
        result=run(a.plan,a.assembly,a.atlas,a.catalog,a.ammunition,a.config,a.bindings,a.out,
                   memory_budget=a.memory_budget*1024**2,aim_entries=a.aim_entries,resume=a.resume,limit=a.limit)
        print(json.dumps(result))
    finally:
        if kernel:kernel.SetThreadExecutionState(0x80000000)


if __name__=='__main__':main()
