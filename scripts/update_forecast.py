#!/usr/bin/env python3
"""Acquire a pinned source manifest and audit old/new catalog changes without mutating either."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.forecast.data import read_json,canonical
from wt_atlas.forecast.updates import acquire,update_report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--before',type=Path);p.add_argument('--after',type=Path)
    p.add_argument('--offline',action='store_true');p.add_argument('--dry-run',action='store_true')
    p.add_argument('--cache',type=Path,default=ROOT/'data/cache/forecast-sources');p.add_argument('--out',type=Path,default=ROOT/'reports/source-update.json')
    a=p.parse_args();m=read_json(a.manifest)
    r={'dry_run':True,'sources':m.get('sources',[])} if a.dry_run else acquire(m,a.manifest.parent,a.cache,offline=a.offline)
    if a.before and a.after:r['changes']=update_report(read_json(a.before),read_json(a.after),m.get('aliases',[]))
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(canonical(r),encoding='utf-8');print(canonical(r))

if __name__=='__main__':main()
