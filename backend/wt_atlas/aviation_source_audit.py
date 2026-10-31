"""Reconcile shipboard reference claims with captured, exact unit pages.

This is an evidence audit only: it never decides Ground RB eligibility, assigns
BR, or removes a vehicle from a release gate.
"""
import gzip
import hashlib
from pathlib import Path
import re

from lxml import html

from .forecast.data import digest, safe_file

ARTICLE='https://wiki.warthunder.com/mechanics/247-shipboard-reconnaissance-aircraft'
SHIP_LEGEND='† - aircraft is available on the naval vessels in the naval battles, but not as an aviation unit'
UNUSED_LEGEND='‡ - not used in the game'


def normalized(value):
    return ' '.join(value.split())


def article_claims(raw):
    document=html.fromstring(raw.decode('utf8'))
    text=normalized(''.join(document.itertext()))
    if SHIP_LEGEND not in text or UNUSED_LEGEND not in text:
        raise ValueError('Shipboard source legend changed')
    claims={}
    for row in document.xpath('//tr'):
        cells=row.xpath('./td')
        if not cells:continue
        cell=cells[0];ids=[]
        for href in cell.xpath('.//a/@href'):
            match=re.fullmatch(r'/unit/([a-z0-9_]+)',href)
            if match:ids.append(match.group(1))
        if len(ids)!=1:continue
        label=''.join(cell.itertext())
        marker='†' if '†' in label else '‡' if '‡' in label else None
        if marker:
            if ids[0] in claims:raise ValueError('Ambiguous shipboard unit identity')
            claims[ids[0]]='shipborne_not_aviation' if marker=='†' else 'unused_in_game'
    return claims


def audit(catalog, gap_report, article_path, article_sha256, wiki_root):
    article_path=Path(article_path);raw=article_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=article_sha256:
        raise ValueError('Shipboard article checksum mismatch')
    receipts=[r for r in gap_report.get('rating_and_eligibility',{}).get('historical_official_evidence',[])
              if r.get('url')==ARTICLE and r.get('sha256')==article_sha256]
    if len(receipts)!=1:raise ValueError('Missing unique official shipboard source receipt')
    claims=article_claims(raw)
    ids=gap_report['rating_and_eligibility']['unknown_ground_rb_rating_ids']
    if len(ids)!=len(set(ids)) or any(not re.fullmatch(r'[a-z0-9_]+',x) for x in ids):
        raise ValueError('Invalid or duplicate unit identities')
    vehicles={v['id']:v for v in catalog['vehicles']}
    if len(vehicles)!=len(catalog['vehicles']):raise ValueError('Duplicate catalog identity')
    rows=[]
    for identifier in ids:
        vehicle=vehicles.get(identifier)
        if vehicle is None:raise ValueError('Unknown catalog unit: '+identifier)
        url='https://wiki.warthunder.com/unit/'+identifier
        if vehicle.get('wiki_url')!=url:raise ValueError('Unit Wiki URL mismatch: '+identifier)
        source=[p for p in vehicle.get('provenance',[]) if p.get('url')==url and p.get('sha256')]
        if len(source)!=1:raise ValueError('Missing exact unit Wiki provenance: '+identifier)
        payload=gzip.decompress(safe_file(Path(wiki_root),'unit_'+identifier+'.html.gz').read_bytes())
        sha=hashlib.sha256(payload).hexdigest()
        if sha!=source[0]['sha256']:raise ValueError('Unit Wiki checksum mismatch: '+identifier)
        name=vehicle.get('name')
        if not isinstance(name,str) or not name:raise ValueError('Missing exact unit name: '+identifier)
        document=html.fromstring(payload.decode('utf8'));introductions=[]
        for paragraph in document.xpath('//p'):
            text=normalized(' '.join(paragraph.itertext()))
            # Require this exact unit as the affirmative subject. A paragraph
            # mentioning another vehicle or saying "not introduced" is not proof.
            pattern=(r'(?<![\w-])'+re.escape(name)+r'\s+(?:was|is|has\s+been)\s+'
                     r'introduced\s+in\s+update\s+(\d+(?:\.\d+)*)\b')
            match=re.search(pattern,text,re.IGNORECASE)
            if match:
                introductions.append({'update':match.group(1),'paragraph_sha256':hashlib.sha256(text.encode('utf8')).hexdigest()})
        claim=claims.get(identifier)
        rows.append({'vehicle_id':identifier,'article_claim':claim,'unit_page_url':url,'unit_page_sha256':sha,
            'explicit_unit_introductions':introductions,
            'article_unit_claim_conflict':claim=='unused_in_game' and bool(introductions),
            'ground_rb_eligibility':'unresolved'})
    return {'schema_version':'1.0.0','article_url':ARTICLE,'article_sha256':article_sha256,
        'gap_source_sha256':digest({'unknown_ground_rb_rating_ids':ids,'shipboard_receipt':receipts[0]}),
        'wiki_collected_at':catalog.get('snapshot',{}).get('wiki_collected_at'),
        'scope':'Exact-source disagreement audit only; no source page supplies same-version independent Ground RB eligibility.',
        'claims':rows,'conflicting_unit_ids':sorted(x['vehicle_id'] for x in rows if x['article_unit_claim_conflict']),
        'ground_rb_eligibility_promoted':False,'publishable':False,'formal_rankings':0}
