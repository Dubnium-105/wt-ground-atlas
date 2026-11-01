#!/usr/bin/env python3
"""Import exact pinned Ground RB rules and report current-client disagreements."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import write_json
from wt_atlas.forecast.rule_snapshot import import_snapshot,capture_snapshot

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--commit',required=True)
    p.add_argument('--client-wpcost',type=Path)
    p.add_argument('--fetch',action='store_true',help='Capture verified blobs from the exact public Git commit')
    p.add_argument('--offline',action='store_true',help='Require an existing complete capture')
    p.add_argument('--catalog-output',type=Path)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.catalog_output and a.catalog_output.resolve()==a.catalog.resolve():
        p.error('Use a separate output catalog; the original snapshot is preserved')
    if a.fetch or a.offline:capture_snapshot(a.source_root,a.commit,offline=a.offline)
    catalog,report=import_snapshot(a.catalog,a.source_root,a.commit,client_wpcost=a.client_wpcost)
    if a.catalog_output:write_json(a.catalog_output,catalog)
    write_json(a.out,report)
    print(json.dumps({'counts':report['counts'],'promoted_release_status':False,'report':str(a.out)}))
if __name__=='__main__':main()
