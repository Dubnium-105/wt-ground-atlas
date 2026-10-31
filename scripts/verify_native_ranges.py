#!/usr/bin/env python3
"""Cross-check actual ordered geometry on CPU and the explicit V2 CUDA adapter."""
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from wt_atlas.common import read_json,write_json,file_hash
from wt_atlas.cpu_model import PreparedLayers
from wt_atlas.cuda_range import prepare
from wt_atlas.forecast.data import safe_file
from wt_atlas.forecast.inventory import bind_geometry
from wt_atlas.range_rebuild import source_range_profile


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('repository','catalog','ammunition','out'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--source-root',type=Path)
    p.add_argument('--targets',nargs='+',required=True)
    p.add_argument('--profiles',nargs='+',required=True)
    p.add_argument('--distances',nargs='+',type=float,default=[100,500,1000])
    p.add_argument('--max-rays',type=int,default=8192)
    p.add_argument('--config',type=Path,default=ROOT/'config/pipeline.json')
    args=p.parse_args();start=time.perf_counter()
    if args.max_rays<1:raise ValueError('Positive sample size required')
    catalog=read_json(args.catalog)
    selected={**catalog,'vehicles':[v for v in catalog['vehicles'] if v['id'] in args.targets]}
    bindings,errors=bind_geometry(args.repository,args.repository/'data/work/client/jobs',selected)
    if errors or set(bindings)!=set(args.targets):raise ValueError('Independent geometry verification failed: '+str(errors))
    profiles={v['name']:v for v in read_json(args.ammunition)}
    config=read_json(args.config);rows=[]
    for target in sorted(bindings):
        job=read_json(args.repository/'data/work/client/jobs'/ (target+'.json'))
        metadata=safe_file(args.repository,job['layer_metadata'].replace('\\','/'))
        cpu=PreparedLayers(metadata,config);gpu=prepare(metadata,ROOT/'backend/vendor/grp_probability_v2')
        # Uniform positions and occupied rays both matter; the latter ensure
        # that an empty background cannot make the parity check vacuous.
        occupied=np.flatnonzero(np.diff(cpu.offsets)>0)
        ids=np.unique(np.r_[np.linspace(0,cpu.n-1,min(args.max_rays,cpu.n),dtype=np.int64),
            occupied[np.linspace(0,len(occupied)-1,min(args.max_rays,len(occupied)),dtype=np.int64)]])
        for name in args.profiles:
            source=profiles[name]
            identity=bindings[target]['identity'];provenance=source['provenance']
            if (identity['client_version'],identity['datamine_commit'])!=(provenance['client_version'],provenance['datamine_commit']):
                raise ValueError('Source geometry/ammunition version mismatch')
            for distance in args.distances:
                ammo=source_range_profile(source,distance,args.source_root)
                actual=gpu.calculate(ammo);expected=cpu.calculate(ammo,ids)
                probability,charge,state=[v.ravel()[ids] for v in actual]
                np.testing.assert_array_equal(expected[2],state)
                np.testing.assert_allclose(expected[0],probability,rtol=0,atol=2/65536,equal_nan=True)
                np.testing.assert_allclose(expected[1],charge,rtol=2e-6,atol=.001,equal_nan=True)
                np.testing.assert_array_equal(expected[3],gpu.risk_flags(ammo).ravel()[ids])
                finite=np.isfinite(probability)
                rows.append({'target':target,'profile':name,'distance_m':distance,'sampled_rays':len(ids),
                    'computed_rays':int(np.count_nonzero(state==3)),
                    'partial_probability_rays':int(np.count_nonzero((probability>0)&(probability<1))),
                    'max_probability_delta':float(np.max(np.abs(expected[0][finite]-probability[finite]),initial=0)),
                    'layers_sha256':file_hash(metadata),'layer_arrays_sha256':cpu.meta['arrays_sha256']})
        cp=gpu.cp;del gpu,cpu;cp.get_default_memory_pool().free_all_blocks()
    report={'status':'passed','checks':rows,'duration_seconds':time.perf_counter()-start,
        'ammunition_sha256':file_hash(args.ammunition),'catalog_sha256':file_hash(args.catalog),
        'cpu_code_sha256':file_hash(ROOT/'backend/wt_atlas/cpu_model.py'),
        'cuda_range_adapter_sha256':file_hash(ROOT/'backend/wt_atlas/cuda_range.py'),
        'probability_tolerance':2/65536,'source_probability_maps_read':0,'game_probability_validated':False}
    write_json(args.out,report)
    print(json.dumps({'status':'passed','checks':len(rows),'sampled_rays':sum(r['sampled_rays'] for r in rows),
        'max_probability_delta':max(r['max_probability_delta'] for r in rows),'duration_seconds':report['duration_seconds']},indent=2))


if __name__=='__main__':main()
