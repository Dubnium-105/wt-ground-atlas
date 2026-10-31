#!/usr/bin/env python3
"""Revalidate actual fixed aircraft payloads from a captured same-version client."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.client_payloads import import_payloads
from wt_atlas.common import write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('catalog','capture','rules-root','catalog-output','out'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--commit',required=True);p.add_argument('--config',type=Path,default=ROOT/'config/sources.json')
    a=p.parse_args()
    if a.catalog.resolve()==a.catalog_output.resolve():raise ValueError('Keep source catalog unchanged')
    catalog,report=import_payloads(a.catalog,a.capture,a.rules_root,a.commit,a.config)
    write_json(a.catalog_output,catalog);write_json(a.out,report)
    print(json.dumps({k:v for k,v in report.items() if k!='vehicles'},indent=2))


if __name__=='__main__':main()
