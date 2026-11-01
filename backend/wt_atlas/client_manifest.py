"""Verify installed client inputs against a captured official updater manifest."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .common import atomic_write, file_hash, read_json, write_json
from .forecast.data import safe_file


VERSION_URL='https://yupmaster.gaijinent.com/yuitem/get_version.php?proj=warthunder&tag='
MANIFEST_URL='https://yupmaster.gaijinent.com/yuitem/get_version_yup.php?proj=warthunder&tag='
MAX_MANIFEST_BYTES=32*1024**2


def bdecode(data):
    """Decode bounded bencode values; reject ambiguous dictionaries and tails."""
    if not isinstance(data,bytes) or len(data)>MAX_MANIFEST_BYTES:
        raise ValueError('Invalid or oversized updater manifest')
    position=0;nodes=0
    def parse(depth):
        nonlocal position,nodes
        nodes+=1
        if depth>64 or nodes>1000000 or position>=len(data):raise ValueError('Invalid bencode structure')
        token=data[position:position+1]
        if token==b'i':
            end=data.find(b'e',position+1)
            raw=data[position+1:end] if end>=0 else b''
            if not re.fullmatch(rb'0|-?[1-9][0-9]*',raw):raise ValueError('Invalid bencode integer')
            position=end+1
            try:return int(raw)
            except ValueError as error:raise ValueError('Oversized bencode integer') from error
        if token in (b'l',b'd'):
            position+=1;result=[] if token==b'l' else {}
            while position<len(data) and data[position:position+1]!=b'e':
                key=parse(depth+1)
                if token==b'l':result.append(key)
                else:
                    if not isinstance(key,bytes) or key in result:raise ValueError('Invalid or duplicate bencode dictionary key')
                    result[key]=parse(depth+1)
            if position>=len(data):raise ValueError('Unterminated bencode container')
            position+=1;return result
        if token.isdigit():
            colon=data.find(b':',position)
            raw=data[position:colon] if colon>=0 else b''
            if not re.fullmatch(rb'0|[1-9][0-9]*',raw):raise ValueError('Invalid bencode byte length')
            try:length=int(raw)
            except ValueError as error:raise ValueError('Oversized bencode byte length') from error
            start=colon+1;end=start+length
            if end>len(data):raise ValueError('Truncated bencode bytes')
            position=end;return data[start:end]
        raise ValueError('Invalid bencode token')
    result=parse(0)
    if position!=len(data):raise ValueError('Trailing bencode content')
    return result


def parse_manifest(data):
    decoded=bdecode(data)
    try:
        info=decoded[b'info'];version=info[b'version'].decode('ascii');project=decoded[b'yup'][b'project']
        yup_version=decoded[b'yup'][b'version'].decode('ascii');rows=info[b'files']
    except (KeyError,TypeError,AttributeError,UnicodeError) as error:
        raise ValueError('Updater manifest fields missing') from error
    if project!=b'warthunder' or not re.fullmatch(r'\d+\.\d+\.\d+\.\d+',version) or yup_version!=version or not isinstance(rows,list):
        raise ValueError('Updater project or version mismatch')
    entries={}
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Invalid updater file entry')
        if b'sha1' not in row:continue
        try:
            parts=[x.decode('utf-8') for x in row[b'path']];size=row[b'length'];sha=row[b'sha1']
        except (KeyError,TypeError,AttributeError,UnicodeError) as error:
            raise ValueError('Invalid updater file identity') from error
        if (not parts or any(not x or x in ('.','..') or '/' in x or '\\' in x for x in parts) or
                type(size) is not int or size<0 or not isinstance(sha,bytes) or len(sha)!=20):
            raise ValueError('Invalid updater file identity')
        path='/'.join(parts)
        if path in entries:raise ValueError('Duplicate updater file path')
        entries[path]={'bytes':size,'sha1':sha.hex()}
    return {'version':version,'entries':entries,'file_entries':len(rows)}


def descriptor(text):
    lines=text.decode('utf-8').split()
    urls=[]
    for value in lines:
        parsed=urlsplit(value);host=parsed.hostname or ''
        if parsed.scheme=='https' and not parsed.username and not parsed.password and (host=='gaijinent.com' or host.endswith('.gaijinent.com')) and re.fullmatch(r'/[0-9a-f]{40}\.yup',parsed.path) and not parsed.query and not parsed.fragment:
            urls.append(value)
    if not urls:raise ValueError('Official updater descriptor has no supported manifest URL')
    names={urlsplit(x).path for x in urls}
    if len(names)!=1:raise ValueError('Updater descriptor lists different manifest identities')
    return urls[0],next(iter(names))[1:-4]


def verify_capture(root):
    root=Path(root);receipt=read_json(root/'capture.json')
    if receipt.get('version_url')!=VERSION_URL or receipt.get('descriptor_url')!=MANIFEST_URL:
        raise ValueError('Updater capture has untrusted source URLs')
    for name in ('version.txt','descriptor.txt','manifest.yup'):
        if file_hash(root/name)!=receipt['files'][name]['sha256'] or (root/name).stat().st_size!=receipt['files'][name]['bytes']:
            raise ValueError('Updater capture changed: '+name)
    version=(root/'version.txt').read_text(encoding='ascii').strip()
    url,expected=descriptor((root/'descriptor.txt').read_bytes())
    body=(root/'manifest.yup').read_bytes();parsed=parse_manifest(body)
    if hashlib.sha1(body).hexdigest()!=expected or parsed['version']!=version or receipt.get('version')!=version or receipt['manifest_url']!=url:
        raise ValueError('Updater manifest/version identity mismatch')
    return receipt,parsed


def capture(root,*,offline=False):
    root=Path(root)
    if (root/'capture.json').is_file():return verify_capture(root)[0]
    if offline:raise ValueError('No complete updater capture available offline')
    def fetch(url,limit):
        request=Request(url,headers={'User-Agent':'WT-Ground-Atlas client source audit'})
        with urlopen(request,timeout=60) as response:
            if response.url!=url:raise ValueError('Unexpected updater redirect')
            data=response.read(limit+1)
        if len(data)>limit:raise ValueError('Updater response exceeds byte budget')
        return data
    version=fetch(VERSION_URL,128);listing=fetch(MANIFEST_URL,4096)
    url,expected=descriptor(listing);body=fetch(url,MAX_MANIFEST_BYTES)
    # A moving production channel is not an atomic version/manifest capture.
    if fetch(VERSION_URL,128)!=version or descriptor(fetch(MANIFEST_URL,4096))[1]!=expected:
        raise ValueError('Updater changed during capture; retry into a new capture')
    parsed=parse_manifest(body)
    if hashlib.sha1(body).hexdigest()!=expected or parsed['version']!=version.decode('ascii').strip():
        raise ValueError('Downloaded updater manifest does not match its version and identity')
    files={}
    for name,data in (('version.txt',version),('descriptor.txt',listing),('manifest.yup',body)):
        atomic_write(root/name,data);files[name]={'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
    receipt={'schema_version':'1.0.0','collected_at':datetime.now(timezone.utc).isoformat(),
        'version_url':VERSION_URL,'descriptor_url':MANIFEST_URL,'manifest_url':url,'version':parsed['version'],'files':files}
    write_json(root/'capture.json',receipt)
    return verify_capture(root)[0]


def file_digests(path):
    sha1=hashlib.sha1();sha256=hashlib.sha256();size=0
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            size+=len(block);sha1.update(block);sha256.update(block)
    return {'bytes':size,'sha1':sha1.hexdigest(),'sha256':sha256.hexdigest()}


def audit(root,client_root,repository,inventory):
    receipt,manifest=verify_capture(root)
    client_root=Path(client_root);repository=Path(repository)
    try:local_manifest=file_digests(client_root/'warthunder.yup')
    except OSError:local_manifest=None
    manifest_sha=receipt['files']['manifest.yup']['sha256']
    packs=[]
    for name in ('aces.vromfs.bin','char.vromfs.bin','gui.vromfs.bin'):
        official=manifest['entries'].get(name)
        try:actual=file_digests(safe_file(client_root,name))
        except (OSError,ValueError):actual=None
        packs.append({'path':name,'official':official,'actual':actual,
            'status':'missing_local_file' if actual is None else 'matched' if official and (official['sha1'],official['bytes'])==(actual['sha1'],actual['bytes']) else 'mismatch_or_missing_manifest_entry'})
    index=defaultdict(list)
    for path,row in manifest['entries'].items():
        if path.endswith('.grp'):index[(row['sha1'],row['bytes'])].append(path)
    groups={};missing=[]
    for item in inventory['items']:
        sha=item.get('grp_sha256');path=item.get('raw_path')
        if not sha or not path:
            missing.append(item.get('vehicle_id'));continue
        key=(sha,path.replace('\\','/'))
        groups.setdefault(key,[]).append(item['vehicle_id'])
    grps=[]
    for (expected,path),vehicles in sorted(groups.items()):
        try:actual=file_digests(safe_file(repository,path))
        except (OSError,ValueError):
            grps.append({'grp_sha256':expected,'vehicles':sorted(vehicles),'manifest_paths':[], 'status':'missing_local_file'})
            continue
        matches=sorted(index[(actual['sha1'],actual['bytes'])])
        status=('local_sha256_mismatch' if actual['sha256']!=expected else 'missing_manifest_entry' if not matches else
            'ambiguous_manifest_entry' if len(matches)>1 else 'matched')
        grps.append({'grp_sha256':expected,'vehicles':sorted(vehicles),'actual_sha1':actual['sha1'],
            'bytes':actual['bytes'],'manifest_paths':matches,'status':status})
    return {'schema_version':'1.0.0','source':receipt,'version':manifest['version'],
        'manifest_file_entries':manifest['file_entries'],'manifest_hashed_files':len(manifest['entries']),
        'installed_manifest_matches':bool(local_manifest and local_manifest['sha256']==manifest_sha),'packs':packs,
        'inventory_version':inventory.get('client_version'),
        'inventory_version_matches':inventory.get('client_version')==manifest['version'],
        'grp_counts':dict(Counter(r['status'] for r in grps)),
        'vehicles_with_grp_identity':sum(len(r['vehicles']) for r in grps),
        'vehicles_matching_manifest':sum(len(r['vehicles']) for r in grps if r['status']=='matched'),
        'vehicles_missing_grp_identity':missing,'grps':grps,'scope':'Manifest byte identity for cached GRPs; active resource and geometry bindings require their independent proofs',
        'promoted_release_status':False,'publishable':False}
