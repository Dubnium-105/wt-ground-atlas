#!/usr/bin/env python3
"""Plan physical coverage from completed verified indexes without publishing results."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import write_json
from wt_atlas.forecast.federated_coverage import audit


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('catalog','ammunition','config','out'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--index',type=Path,action='append',required=True)
    parser.add_argument('--delivery-aware',action='store_true',help='Withhold choices outside source-declared missile distance bounds; do not assume remaining flight is verified')
    args=parser.parse_args()
    if args.out.resolve() in {p.resolve() for p in (args.catalog,args.ammunition,args.config,*args.index)}:
        raise ValueError('Keep coverage output separate from source inputs')
    if any(p.is_symlink() for p in (args.out,*args.out.parents)):
        raise ValueError('Symlink coverage output')
    for index in args.index:
        db=sqlite3.connect(index.resolve().as_uri()+'?mode=ro',uri=True)
        try:root=db.execute("SELECT value FROM settings WHERE key='root'").fetchone()
        finally:db.close()
        if not root or args.out.resolve().is_relative_to(Path(root[0]).resolve()):
            raise ValueError('Keep coverage output outside every source atlas')
    report=audit(args.catalog,args.ammunition,args.config,args.index,delivery_aware=args.delivery_aware)
    write_json(args.out,report)
    print(json.dumps({k:report[k] for k in ('counts','blockers','source_arrays_rechecked','publishable')},indent=2))


if __name__=='__main__':main()
