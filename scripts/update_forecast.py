#!/usr/bin/env python3
"""Acquire pinned sources or refresh a separate exact-ID Wiki catalog snapshot."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.forecast.data import read_json,canonical
from wt_atlas.forecast.updates import acquire,update_report
from wt_atlas.wiki_refresh import capture_wiki,refresh_catalog,atomic_json

def main():
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--manifest',type=Path)
    source.add_argument('--refresh-wiki',action='store_true')
    p.add_argument('--before',type=Path);p.add_argument('--after',type=Path)
    p.add_argument('--sources-config',type=Path,default=ROOT/'config/sources.json')
    p.add_argument('--catalog-output',type=Path)
    p.add_argument('--offline',action='store_true');p.add_argument('--dry-run',action='store_true')
    p.add_argument('--cache',type=Path,default=ROOT/'data/cache/forecast-sources');p.add_argument('--out',type=Path,default=ROOT/'reports/source-update.json')
    a=p.parse_args()
    protected=[path.resolve() for path in (a.before,a.after,a.manifest,a.sources_config) if path]
    if a.out.resolve() in protected:p.error('Report output would replace an input')
    if a.refresh_wiki:
        if not a.before or not a.catalog_output or a.after:p.error('--refresh-wiki requires --before and --catalog-output; do not use --after')
        if a.catalog_output.resolve() in protected or a.catalog_output.resolve()==a.out.resolve():p.error('Catalog output would replace an input/report')
        if a.catalog_output.resolve().is_relative_to(a.cache.resolve()):p.error('Keep catalog output outside the source cache')
        try:
            manifest,vehicles=capture_wiki(read_json(a.sources_config),a.cache,offline=a.offline or a.dry_run)
            catalog,receipt=refresh_catalog(read_json(a.before),manifest,vehicles)
            if not a.dry_run:atomic_json(a.catalog_output,catalog)
            r={'ready':True,'dry_run':a.dry_run,'catalog_written':not a.dry_run,
               'catalog_output':str(a.catalog_output),'receipt':receipt}
        except (OSError,ValueError,KeyError,TypeError) as error:
            r={'ready':False,'dry_run':a.dry_run,'catalog_written':False,'error':str(error)}
        atomic_json(a.out,r)
        print(canonical({k:v for k,v in r.items() if k!='receipt'}))
        if not r['ready']:p.exit(1)
        return
    m=read_json(a.manifest)
    r={'dry_run':True,'sources':m.get('sources',[])} if a.dry_run else acquire(m,a.manifest.parent,a.cache,offline=a.offline)
    if a.before and a.after:r['changes']=update_report(read_json(a.before),read_json(a.after),m.get('aliases',[]))
    atomic_json(a.out,r);print(canonical(r))

if __name__=='__main__':main()
