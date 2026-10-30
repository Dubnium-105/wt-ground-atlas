"""Pinned offline source acquisition, cached fallback and exact-ID change auditing."""
from datetime import datetime,timezone
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
from .data import canonical,digest,file_hash,read_json,safe_file
from .versions import snapshot,invalidation


def acquire(manifest,base,cache,*,offline=False,timeout=30,retries=2,max_bytes=128*1024**2):
    snapshot({'snapshot':manifest})
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    rows=[]
    for item in manifest.get('sources',[]):
        row={**item,'status':'blocked'}
        try:
            expected=item['sha256']
            if not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('A pinned SHA-256 is required')
            if item.get('game_version')!=manifest['game_version']:raise ValueError('Source game version mismatch')
            target=cache/expected
            if target.is_file() and file_hash(target)==expected:
                row.update(status='cached_verified',cache_file=str(target));rows.append(row);continue
            if item.get('path'):
                source=safe_file(Path(base),item['path'])
                if source.stat().st_size>max_bytes:raise ValueError('Source byte budget exceeded')
                payload=source.read_bytes()
            else:
                url=item['url'];parsed=urlsplit(url)
                if parsed.scheme!='https' or parsed.username or parsed.password:raise ValueError('Source must be HTTPS without credentials')
                host=parsed.hostname or ''
                if item['kind']=='datamine':
                    commit=manifest.get('datamine_commit','')
                    if not re.fullmatch('[0-9a-f]{40}',commit) or host!='raw.githubusercontent.com' or f'/War-Thunder-Datamine/{commit}/' not in parsed.path:raise ValueError('Datamine URL must be pinned to the declared commit')
                elif host!='warthunder.com' and not host.endswith('.warthunder.com'):raise ValueError('Official Wiki/announcement host required')
                if offline:raise ValueError('Pinned capture absent in offline cache')
                for attempt in range(retries+1):
                    try:
                        with urlopen(Request(url,headers={'User-Agent':'WT-Ground-Atlas offline source audit'}),timeout=timeout) as response:
                            if urlsplit(response.url).hostname!=host:raise ValueError('Source redirected to a different host')
                            payload=response.read(max_bytes+1)
                        break
                    except OSError:
                        if attempt==retries:raise
                        time.sleep(min(2**attempt,4))
            if len(payload)>max_bytes:raise ValueError('Source byte budget exceeded')
            import hashlib
            if hashlib.sha256(payload).hexdigest()!=expected:raise ValueError('Pinned source hash mismatch')
            partial=target.with_suffix('.partial');partial.write_bytes(payload);os.replace(partial,target)
            row.update(status='verified',cache_file=str(target),bytes=len(payload))
        except (ValueError,KeyError,OSError) as exc:row['error']=str(exc)
        rows.append(row)
    return {'snapshot':snapshot({'snapshot':manifest}),'sources':rows,'collected_at':datetime.now(timezone.utc).isoformat(),
            'ready':bool(rows) and all(r['status'] in ('verified','cached_verified') for r in rows)}


def audit_aliases(catalog,aliases):
    ids={v['id'] for v in catalog['vehicles']};seen={};conflicts=[];accepted=[]
    for row in aliases:
        key=row.get('alias');target=row.get('vehicle_id')
        if not key or target not in ids or not row.get('source'):
            conflicts.append({**row,'reason':'missing_exact_id_or_source'});continue
        if key in seen and seen[key]!=target:conflicts.append({**row,'reason':'ambiguous_alias'});continue
        seen[key]=target;accepted.append(row)
    bad={r.get('alias') for r in conflicts}
    return {'accepted':[r for r in accepted if r['alias'] not in bad],'conflicts':conflicts,'fuzzy_matches_used':False}


def update_report(before,after,aliases=()):
    return {'before':snapshot(before),'after':snapshot(after),'why_rebuilt':invalidation(before,after),
            'aliases':audit_aliases(after,aliases),'catalog_hashes':[digest(before),digest(after)]}
