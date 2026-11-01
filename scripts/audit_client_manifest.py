#!/usr/bin/env python3
"""Capture the official updater manifest and audit local client/GRP bytes."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import read_json,write_json,file_hash
from wt_atlas.client_manifest import audit,capture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('capture-root','client-root','repository','inventory','out'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--fetch',action='store_true')
    args=parser.parse_args()
    output=args.out.resolve(); captured=args.capture_root.resolve()
    protected=(args.client_root.resolve(),args.repository.resolve())
    if (output==args.inventory.resolve() or captured in (output,*output.parents)
            or any(root in (path,*path.parents) for root in protected for path in (output,captured))):
        raise ValueError('Keep captured sources and audit output separate from client and repository inputs')
    capture(args.capture_root,offline=not args.fetch)
    result=audit(args.capture_root,args.client_root,args.repository,read_json(args.inventory))
    result['inventory_file_sha256']=file_hash(args.inventory)
    write_json(args.out,result)
    print(json.dumps({k:result[k] for k in ('version','installed_manifest_matches','inventory_version_matches','grp_counts','vehicles_matching_manifest','publishable')},indent=2))


if __name__=='__main__':main()
