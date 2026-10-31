"""Import pinned Ground RB ratings without promoting a mixed physics snapshot.

Field selection and rank conversion follow get_unit_blk_economic_rank_by_mode
and calcBattleRatingFromRank in the captured client ranks_common_shared.nut.
The input script hash pins the semantics, not just a claimed version string.
"""
from __future__ import annotations
import copy
from collections import Counter
import re
import hashlib
import json
import urllib.request
from datetime import datetime,timezone
from pathlib import Path
from .data import digest, file_hash, read_json
from ..common import atomic_write,write_json
from .mount_rules import audit_vehicle_mounts

RANKS_SCRIPT_SHA256='f4ba585b2260289bb7eb4b9e8d62b9357b6c1d6e36e7a4b44a67c21660008c9e'
WPCOST_PATH='char.vromfs.bin_u/config/wpcost.blkx'
RANKS_PATH='gui.vromfs.bin_u/globals/ranks_common_shared.nut'
SOURCE_PATHS=('version',WPCOST_PATH,RANKS_PATH,'char.vromfs.bin_u/config/warpoints.blkx')
MAX_SOURCE_BYTES=64*1024**2


def verify_capture(root,commit):
    receipt=read_json(root/'capture.json')
    if receipt.get('commit')!=commit:raise ValueError('Captured rule commit mismatch')
    files=receipt.get('files',{})
    for path in SOURCE_PATHS:
        row=files.get(path,{})
        url='https://raw.githubusercontent.com/gszabi99/War-Thunder-Datamine/'+commit+'/'+path
        file=root/path
        if row.get('url')!=url or not file.is_file() or file_hash(file)!=row.get('sha256'):
            raise ValueError('Captured rule source checksum or URL mismatch: '+path)
        data=file.read_bytes()
        blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if blob!=row.get('git_blob_sha'):raise ValueError('Pinned Git blob mismatch: '+path)
    return receipt


def capture_snapshot(root,commit,*,offline=False):
    """Acquire four exact Git blobs; an incomplete transfer gets no receipt."""
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('An exact Datamine commit is required')
    root=Path(root)
    if (root/'capture.json').exists():
        return verify_capture(root,commit)
    if offline:raise ValueError('No complete pinned rule capture is available offline')
    if any(p.is_symlink() for p in (root,*root.parents)):raise ValueError('Symlink source capture root')
    root.mkdir(parents=True,exist_ok=True)
    files={}
    def fetch(url,limit):
        request=urllib.request.Request(url,headers={'User-Agent':'WT-Ground-Atlas/1.0 pinned rule capture'})
        with urllib.request.urlopen(request,timeout=30) as response:data=response.read(limit+1)
        if len(data)>limit:raise ValueError('Rule source exceeds download limit')
        return data
    for path in SOURCE_PATHS:
        api='https://api.github.com/repos/gszabi99/War-Thunder-Datamine/contents/'+path+'?ref='+commit
        metadata=json.loads(fetch(api,2*1024**2))
        url='https://raw.githubusercontent.com/gszabi99/War-Thunder-Datamine/'+commit+'/'+path
        file=root/path
        data=file.read_bytes() if file.is_file() and file.stat().st_size<=MAX_SOURCE_BYTES else b''
        cached_blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if cached_blob!=metadata.get('sha') or len(data)!=metadata.get('size'):
            data=fetch(url,MAX_SOURCE_BYTES)
        blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if metadata.get('type')!='file' or metadata.get('sha')!=blob or metadata.get('size')!=len(data):
            raise ValueError('Downloaded data does not match the pinned Git object: '+path)
        if path.endswith('.blkx'):json.loads(data)
        atomic_write(root/path,data)
        files[path]={'url':url,'sha256':hashlib.sha256(data).hexdigest(),'git_blob_sha':blob,'bytes':len(data)}
    receipt={'commit':commit,'collected_at':datetime.now(timezone.utc).isoformat(),'files':files}
    write_json(root/'capture.json',receipt)
    return verify_capture(root,commit)


def rating(unit):
    # All Ground RB units use TankHistorical with Historical as the explicit
    # client fallback. Missing ranks remain missing instead of client UI's 0.
    field=next((k for k in ('economicRankTankHistorical','economicRankHistorical') if k in unit),None)
    if field is None:return None,None
    value=unit[field]
    if not isinstance(value,int) or isinstance(value,bool) or value<0:
        raise ValueError('Invalid source economic rank')
    return round(value/3+1,1),field


def reconcile(catalog,wpcost,source):
    """Keep all catalog IDs and null/nonselectable BRs; audit case-only bindings."""
    index={}
    for source_id,unit in wpcost.items():
        if not isinstance(unit,dict):continue
        if not isinstance(source_id,str) or not source_id.isascii():raise ValueError('Unsupported source ID')
        key=source_id.lower()
        if key in index:raise ValueError('Ambiguous case-normalized source ID: '+key)
        index[key]=(source_id,unit)
    result=copy.deepcopy(catalog);rows=[];counts=Counter();mount_rows=[]
    for vehicle in result['vehicles']:
        identifier=vehicle['id']
        if not isinstance(identifier,str) or not identifier.isascii():raise ValueError('Unsupported catalog ID')
        pair=index.get(identifier.lower())
        if pair is None:
            rows.append({'vehicle_id':identifier,'status':'source_unit_missing'});counts['source_unit_missing']+=1;continue
        source_id,unit=pair
        if unit.get('country')!='country_'+vehicle['nation']:
            raise ValueError('Source country conflicts with exact vehicle ID: '+identifier)
        mount_rows.append(audit_vehicle_mounts(vehicle,unit,source))
        br,field=rating(unit);old=vehicle.get('br',{}).get('current')
        status='source_rating_missing' if br is None else 'catalog_nonselectable' if old is None else 'matched' if old==br else 'updated'
        binding={'source_id':source_id,'catalog_id':identifier,
                 'method':'exact_id' if source_id==identifier else 'unique_ascii_case_only_id',
                 'source':source['wpcost_url'],'source_sha256':source['wpcost_sha256']}
        row={'vehicle_id':identifier,'binding':binding,'status':status,'catalog_br':old,'source_br':br,'source_field':field}
        vehicle['rule_snapshot_reference']={**row,'source_version':source['version'],'datamine_commit':source['commit']}
        if br is not None and old is not None:
            vehicle['br']['current']=br
            if vehicle['br'].get('planned')==old:vehicle['br']['planned']=br
            vehicle['br_provenance']={'source':source['wpcost_url'],'source_version':source['version'],
                'sha256':source['wpcost_sha256'],'source_field':field,'source_id':source_id,
                'status':'pinned_datamine_rule','mode':'ground_rb','adapter_source_sha256':source['ranks_sha256']}
        rows.append(row);counts[status]+=1
    snapshot=result.setdefault('snapshot',{})
    snapshot['rule_snapshot']={**source,'catalog_before_sha256':digest(catalog),
        'physics_compatibility':'not_established_by_rating_import',
        'source_status':'pinned_source_not_release_identity'}
    # Updating four BRs cannot prove the client/GRP/loadout snapshot is coherent.
    # Deliberately do not set game_version, released, or official_verified here.
    return result,{'source':source,'counts':dict(counts),'vehicles':rows,
                   'mount_constraints':{'vehicles':mount_rows,'counts':dict(Counter(
                       row['status'] for vehicle in mount_rows for row in vehicle['loadouts']))},
                   'before_sha256':digest(catalog),'after_sha256':digest(result),
                   'promoted_physics_version':False,'promoted_release_status':False}


def import_snapshot(catalog_path,source_root,commit,*,client_wpcost=None):
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('An exact Datamine commit is required')
    root=Path(source_root);capture=verify_capture(root,commit)
    version=(root/'version').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+',version):raise ValueError('Invalid pinned game version')
    ranks=root/RANKS_PATH;wpcost_path=root/WPCOST_PATH
    if file_hash(ranks)!=RANKS_SCRIPT_SHA256:raise ValueError('Unreviewed client rating semantics')
    base='https://raw.githubusercontent.com/gszabi99/War-Thunder-Datamine/'+commit+'/'
    source={'version':version,'commit':commit,'version_sha256':file_hash(root/'version'),
            'wpcost_sha256':file_hash(wpcost_path),'wpcost_url':base+WPCOST_PATH,
            'ranks_sha256':file_hash(ranks),'ranks_url':base+RANKS_PATH,'collected_at':capture['collected_at'],
            'capture_sha256':digest(capture)}
    wpcost=read_json(wpcost_path)
    if client_wpcost:
        from ..native_binding.ordered_blk import parse_ordered_blk,as_json
        client=as_json(parse_ordered_blk(client_wpcost))
        different=sorted(k for k in client.keys()|wpcost.keys() if client.get(k)!=wpcost.get(k))
        source['client_comparison']={'path':str(client_wpcost),'sha256':file_hash(client_wpcost),
            'all_records_equal':not different,'different_records':different,
            'scope':'Extracted char configuration only; not geometry or release identity.'}
    return reconcile(read_json(catalog_path),wpcost,source)
