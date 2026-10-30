#!/usr/bin/env python3
"""Read a shot/pair/mission/lineup evidence node from the offline audit cache."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from wt_atlas.forecast.engine import ShotCache

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cache',type=Path,required=True);p.add_argument('--key',required=True);a=p.parse_args()
    if not a.cache.is_file():p.error('Evidence cache does not exist')
    cache=ShotCache(a.cache)
    try:
        result=cache.evidence_get(a.key)
        print(json.dumps(result or {'status':'missing','key':a.key},ensure_ascii=False,indent=2,allow_nan=False))
    finally:cache.close()

if __name__=='__main__':main()
