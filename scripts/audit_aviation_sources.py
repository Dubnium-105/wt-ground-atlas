#!/usr/bin/env python3
"""Find explicit claim conflicts in captured aviation eligibility sources."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.aviation_source_audit import audit
from wt_atlas.common import file_hash,read_json,write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('catalog','gap-report','article','wiki-root','out'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--article-sha256',required=True)
    args=parser.parse_args()
    if args.out.resolve() in {args.catalog.resolve(),args.gap_report.resolve(),args.article.resolve()}:
        raise ValueError('Keep audit output separate from source inputs')
    inputs=(args.catalog,args.gap_report,args.article)
    hashes={path:file_hash(path) for path in inputs}
    result=audit(read_json(args.catalog),read_json(args.gap_report),args.article,args.article_sha256,args.wiki_root)
    if any(file_hash(path)!=hashes[path] for path in inputs):raise ValueError('Audit input changed during reading')
    result['catalog_sha256']=hashes[args.catalog]
    result['code_sha256']={'audit':file_hash(ROOT/'backend/wt_atlas/aviation_source_audit.py'),
                           'cli':file_hash(Path(__file__))}
    write_json(args.out,result)
    print(json.dumps({'checked':len(result['claims']),'conflicting_unit_ids':result['conflicting_unit_ids'],
                      'ground_rb_eligibility_promoted':False},indent=2))


if __name__=='__main__':main()
