#!/usr/bin/env python3
"""Reproducible synthetic index/load/search benchmark; never claims real-world throughput."""
import argparse
import copy
import itertools
import json
from pathlib import Path
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'backend')]
from test_forecast import fixture,settings
from wt_atlas.forecast.data import Atlas,canonical,digest,read_json
from wt_atlas.forecast.inventory import Inventory
from wt_atlas.forecast.engine import Forecast,ShotCache
from wt_atlas.forecast.search import Optimizer,feasible
from wt_atlas.forecast.resources import usage


def run(sizes):
    records=[]
    for count in sizes:
        with tempfile.TemporaryDirectory(prefix='wt-index-benchmark-') as t:
            root=Path(t);cat,aroot,ammo=fixture(root)
            base=next((aroot/'arrays').glob('*.json'));meta=read_json(base);vehicle=next(v for v in cat['vehicles'] if v['id']==meta['target_id'])
            for p in (aroot/'arrays').glob('*.json'):p.unlink()
            vehicles=[]
            for i in range(count):
                v={**vehicle,'id':f'synthetic_target_{i}'};vehicles.append(v)
                m={**meta,'map_id':digest(i),'target_id':v['id']}
                (aroot/'arrays'/(m['map_id']+'.json')).write_text(canonical(m),encoding='utf-8')
            cat['vehicles']=vehicles
            index=Inventory(root/'index.sqlite');start=time.perf_counter()
            cold=index.sync(aroot,ammo,cat);warm=index.sync(aroot,ammo,cat)
            atlas=Atlas(aroot,ammo,cat,index=index)
            for key in itertools.islice(atlas.meta,3):atlas.load(key)
            records.append({'objects':count,'cold':cold['metrics'],'warm':warm['metrics'],'npz_loads':atlas.loads,
                'selected_maps':cold['selected_maps'],'index_bytes':(root/'index.sqlite').stat().st_size,
                'duration_seconds':time.perf_counter()-start,'resources':usage()})
            index.close()
    with tempfile.TemporaryDirectory(prefix='wt-search-benchmark-') as t:
        root=Path(t);cat,aroot,ammo=fixture(root);atlas=Atlas(aroot,ammo,cat);cache=ShotCache(root/'shots.sqlite');c=settings()
        engine=Forecast(cat,atlas,c,cache);optimizer=Optimizer(engine)
        pool=[v for v in engine.variants if v['nation']=='a']
        oracle=max(optimizer.evaluate(tuple(sorted(v['key'] for v in ls)))['estimate'] for n in (1,2) for ls in itertools.combinations(pool,n) if feasible(ls,2))
        experiments=[]
        for budget in (1,4,64):
            for seed in (17,105):
                cfg=copy.deepcopy(c);cfg['search'].update(max_evaluations=budget,seed=seed)
                e=Forecast(cat,atlas,cfg,cache);start=time.perf_counter();result=Optimizer(e).discover('a',28,2,'all')
                best=result['candidates'][0]['estimate'] if result['candidates'] else None
                experiments.append({'budget':budget,'seed':seed,'evaluations':result['evaluations'],'truncated':result['budget_exhausted'],
                    'best':best,'oracle':oracle,'optimality_gap':oracle-best if best is not None else None,'seconds':time.perf_counter()-start})
        cache.close()
    return {'schema_version':'1.0.0','data_kind':'synthetic_only','metadata':records,'search':experiments,
            'global_optimum_claimed':False,'real_116k_throughput':None,'resources':usage()}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sizes',type=int,nargs='+',default=[1000,10000]);p.add_argument('--out',type=Path,default=ROOT/'reports/search-benchmark.json');a=p.parse_args()
    if any(n<1 for n in a.sizes):p.error('sizes must be positive')
    r=run(a.sizes);a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(canonical(r),encoding='utf-8');print(json.dumps(r,indent=2))

if __name__=='__main__':main()
