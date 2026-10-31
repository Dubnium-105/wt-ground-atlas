#!/usr/bin/env python3
"""Audit exact native-physics input Git blobs across two locally captured commits."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import read_json,write_json,file_hash
from wt_atlas.forecast.version_source_audit import audit,source_tree


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',required=True,type=Path)
    parser.add_argument('--old-commit',required=True);parser.add_argument('--new-commit',required=True)
    parser.add_argument('--ammunition',required=True,type=Path);parser.add_argument('--bindings',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    if args.out.resolve() in (args.ammunition.resolve(),args.bindings.resolve()):
        raise ValueError('Keep source files and audit output separate')
    old=source_tree(args.repository,args.old_commit);new=source_tree(args.repository,args.new_commit)
    result=audit(old,new,read_json(args.ammunition),read_json(args.bindings)['bindings'])
    result.update(ammunition_file_sha256=file_hash(args.ammunition),bindings_file_sha256=file_hash(args.bindings))
    write_json(args.out,result)
    print(json.dumps({k:result[k] for k in ('profile_counts','target_counts','client_grp_equivalence_verified','publishable')},indent=2))


if __name__=='__main__':main()
