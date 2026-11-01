"""Validate and shard completed system-member batch results for a static site."""
from collections import defaultdict
import gzip
import hashlib
from pathlib import Path

from .common import content_hash, file_hash, read_json
from .forecast.data import canonical, safe_file
from .system_study import build_study, prepare_study


def build_batch(batch, plan_path, catalog, ammunition, bindings, config, source_root, destination):
    batch=Path(batch).resolve();plan_path=Path(plan_path).resolve();source_root=Path(source_root).resolve()
    marker=batch/'.wt-system-batch.json';progress=batch/'progress.json'
    identity=read_json(marker);state=read_json(progress);plan=read_json(plan_path)
    requests=plan.get('requests',[])
    if (identity.get('publishable') is not False or identity.get('plan_sha256')!=file_hash(plan_path) or
        state.get('status')!='complete' or state.get('total')!=len(requests) or state.get('processed')!=len(requests) or not requests):
        raise ValueError('System batch is incomplete or has a different plan')
    inputs=[marker,progress,plan_path,Path(catalog),Path(ammunition),Path(bindings),Path(config)]
    hashes={p:file_hash(p) for p in inputs};groups=defaultdict(list);seen=set();report_hashes={}
    code={safe_file(source_root,name):sha for name,sha in identity.get('source_code_sha256_lf',{}).items()}
    def code_matches():
        return bool(code) and all(hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()==sha for p,sha in code.items())
    if not code_matches():raise ValueError('System batch source code changed')
    for request in requests:
        key=content_hash({'batch_identity':identity,'request':request})
        if key in seen:raise ValueError('Duplicate system batch request')
        seen.add(key);path=safe_file(batch,key+'.json');checksum=safe_file(batch,key+'.sha256')
        expected=read_json(checksum)
        if not isinstance(expected,str) or len(expected)!=64 or file_hash(path)!=expected:raise ValueError('System batch result checksum mismatch')
        report=read_json(path)
        if (report.get('batch_job_key')!=key or any(report.get(k)!=request.get(k) for k in ('system_id','attacker_id','ammo_id')) or
            report.get('runtime')!=identity.get('runtime') or report.get('skill_profile')!=identity.get('skill_profile') or report.get('input_sha256')!=identity.get('input_sha256') or
            report.get('source_code_sha256_lf')!=identity.get('source_code_sha256_lf')):
            raise ValueError('System batch result identity mismatch')
        report_hashes[path]=expected;report_hashes[checksum]=file_hash(checksum)
        groups[(report['attacker_id'],report['ammo_id'],report['skill_profile'])].append(path)
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True);choices=[];version=None
    context=prepare_study(catalog,ammunition,bindings,config)
    for group,paths in sorted(groups.items()):
        study=build_study(paths,catalog,ammunition,bindings,config,source_root,_context=context)
        if version is not None and study['source_version']!=version:raise ValueError('System batch version mismatch')
        version=study['source_version'];raw=canonical(study).encode();compressed=gzip.compress(raw,mtime=0)
        sha=hashlib.sha256(compressed).hexdigest();name='system-study-'+sha[:20]+'.json.gz'
        (destination/name).write_bytes(compressed)
        choices.append({'attacker_id':group[0],'attacker_name':study['attacker_name'],'ammo_id':group[1],
            'ammo_name':study['ammo_name'],'skill_profile':group[2],'systems':len(study['systems']),
            'data':{'url':'data/system-study/'+name,'sha256':sha,'bytes':len(compressed),
                    'decoded_sha256':hashlib.sha256(raw).hexdigest(),'decoded_bytes':len(raw),'encoding':'gzip'}})
    if any(file_hash(p)!=h for p,h in {**hashes,**report_hashes}.items()) or not code_matches():raise ValueError('System batch changed during composition')
    return {'schema_version':'1.0.0','kind':'batch','source_version':version,'choices':choices,
            'candidate_count':len(requests),'target_selection_weights':'unknown','publishable':False,'formal_rankings':0}
