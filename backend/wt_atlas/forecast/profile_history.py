"""Reconcile only nonphysical audit annotations against exact historical hashes.

Never infer a missing profile, ownership, source identity or physical parameter.
Both full documents remain hashed; the NPZ and its declared profile hash remain
unchanged. History is explicit and isolated per baseline/planned snapshot.
"""
import copy
from pathlib import Path
from .data import digest,file_hash,number,read_json

MATCH_METHODS={'exact_projectile_name_and_physical_fields','exact_weapon_mount_and_unique_physical_fields'}


def annotation_equivalence(before,after):
    a,b=copy.deepcopy(before),copy.deepcopy(after)
    if a.get('name')!=b.get('name') or a.get('mode') not in ('KE','CE') or a.get('reference_only'):
        return False
    if not number(a.get('distance_m')) or a['distance_m']<0:return False
    ap,bp=a.get('provenance',{}),b.get('provenance',{})
    if not isinstance(ap,dict) or not isinstance(bp,dict):return False
    if not ap.get('penetration_source_ammo_ids') or not ap.get('client_version') or not ap.get('datamine_commit'):
        return False
    for p in (ap,bp):
        if p.get('catalog_physical_mismatches') not in (None,{}):return False
        if p.get('v2_equivalent_candidates') not in (None,[]):return False
        p.pop('catalog_physical_mismatches',None);p.pop('v2_equivalent_candidates',None)
    if ap.get('source_match_method')!=bp.get('source_match_method'):
        if ap.get('source_match_method') not in MATCH_METHODS or bp.get('source_match_method') not in MATCH_METHODS:
            return False
        ap.pop('source_match_method');bp.pop('source_match_method')
    # Includes source weapon hashes, all owned IDs, assumptions, version, range,
    # mechanism, caliber and every other known or future field. No tolerance.
    return digest(a)==digest(b)


class ProfileHistory:
    def __init__(self,paths=()):
        self.profiles={};self.sources={};self.receipts=[];self._proofs={}
        for path in paths:
            path=Path(path);data=read_json(path)
            if isinstance(data,dict):data=[data]
            if not isinstance(data,list) or any(not isinstance(p,dict) or not isinstance(p.get('name'),str) for p in data):
                raise ValueError('Historical profile files must contain complete named profiles')
            sha=file_hash(path)
            self.receipts.append({'path':str(path),'sha256':sha,'profiles':len(data)})
            for profile in data:
                key=digest(profile);self.profiles[key]=profile
                self.sources[key]=min(sha,self.sources.get(key,sha))
        self.signature=digest(sorted(set(r['sha256'] for r in self.receipts)))

    def reconcile(self,declared_hash,current):
        current_hash=digest(current)
        if declared_hash==current_hash:return None
        key=(declared_hash,current_hash)
        if key not in self._proofs:
            historical=self.profiles.get(declared_hash)
            if historical is None or not annotation_equivalence(historical,current):
                self._proofs[key]=False
            else:
                self._proofs[key]={'kind':'exact_historical_profile_annotation_reconciliation',
                    'historical_profile_sha256':declared_hash,'active_profile_sha256':current_hash,
                    'historical_file_sha256':self.sources[declared_hash],
                    'physical_and_ownership_fields_unchanged':True,'npz_modified':False}
        proof=self._proofs[key]
        if proof is False:raise ValueError('profile_hash_mismatch')
        return proof
