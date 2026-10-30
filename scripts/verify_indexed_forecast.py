"""Resume array verification after a completed metadata inventory.

This command verifies the indexed bytes. Run forecast_data inventory after source
metadata changes; a later build always refreshes metadata before selecting maps.
"""
import argparse
from pathlib import Path
import sys
import json
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.forecast.inventory import Inventory,bind_geometry
from wt_atlas.forecast.data import read_json,canonical

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('index','atlas','catalog','bindings','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--max-workers',type=int,default=2);p.add_argument('--memory-budget',type=int,default=1024)
    a=p.parse_args();start=time.perf_counter();index=Inventory(a.index)
    try:
        source=read_json(a.bindings)
        bound,errors=bind_geometry(Path(source['repository']),Path(source['jobs']),read_json(a.catalog))
        index.apply_bindings(bound)
        r=index.verify(a.atlas,max_workers=a.max_workers,memory_budget=a.memory_budget*1024**2,resume=True)
        r.update(binding_count=len(bound),binding_errors=errors,duration_seconds=time.perf_counter()-start)
        a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(canonical(r),encoding='utf-8');print(json.dumps(r,indent=2))
    finally:index.close()

if __name__=='__main__':main()
