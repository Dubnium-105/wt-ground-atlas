"""Bounds-checked GRP2/3 resource directory and dependency records.

Offsets in serialized patchable tables point from the beginning of the file.
Name count and real-resource count are independent. ResEntry.resId selects a
name; it must not be replaced by the entry ordinal. Public Dagor grpData.h is
layout guidance, not proof of equivalence to an arbitrary client revision.
"""
from __future__ import annotations
import struct


def parse_grp_directory(data:bytes)->dict:
    if len(data)<64 or data[:4] not in [b'GRP2',b'GRP3']:raise ValueError('Unsupported or truncated GRP directory')
    version=data[:4].decode();desc,full,rest=struct.unpack_from('<III',data,4)
    if full+16>len(data) or desc<48 or desc>full:raise ValueError('Truncated or invalid GRP descriptor size')
    limit=full+16
    def span(offset,count,stride):
        if count>1_000_000 or offset<16 or offset+count*stride>limit:raise ValueError('GRP table is outside descriptor')
        return offset
    def table(at,stride):
        offset,count=struct.unpack_from('<II',data,at);span(offset,count,stride);return offset,count
    names_at,names_count=table(16,4);entries_at,entries_count=table(32,12)
    dependency_stride=24 if version=='GRP2' else 16
    deps_at,deps_count=table(48,dependency_stride)
    names=[]
    for i in range(names_count):
        offset=struct.unpack_from('<i',data,names_at+i*4)[0]
        if offset==-1:names.append(None);continue
        if offset<64 or offset>=limit:raise ValueError('GRP name pointer is outside descriptor')
        end=data.find(b'\0',offset,limit)
        if end<0:raise ValueError('Unterminated GRP name')
        names.append(data[offset:end].decode('utf8'))
    entries=[]
    for i in range(entries_count):
        cls,offset,name_id,reserved=struct.unpack_from('<IIHH',data,entries_at+i*12)
        if name_id>=len(names) or names[name_id] is None:raise ValueError('Invalid GRP entry name ID')
        if offset<limit or offset>=rest+16:raise ValueError('GRP resource payload offset is outside full pack')
        entries.append({'ordinal':i,'name_id':name_id,'name':names[name_id],'class_id':cls,
                        'offset':offset,'reserved':reserved})
    offsets=sorted({entry['offset'] for entry in entries})
    ends=dict(zip(offsets,offsets[1:]+[rest+16]))
    for entry in entries:entry['end']=ends[entry['offset']]
    dependencies=[]
    for i in range(deps_count):
        at=deps_at+i*dependency_stride
        if version=='GRP2':
            cls,name_id,reserved,refs_at,refs_count,reserved1,reserved2=struct.unpack_from('<IHHIIII',data,at)
            reserved_fields=[reserved,reserved1,reserved2]
        else:
            cls,name_id,refs_count,refs_at=struct.unpack_from('<IHHQ',data,at);reserved_fields=[]
        if name_id>=len(names) or names[name_id] is None:raise ValueError('Invalid GRP dependency owner')
        if refs_count:span(refs_at,refs_count,2)
        ref_ids=list(struct.unpack_from('<'+'H'*refs_count,data,refs_at)) if refs_count else []
        if any(ref!=0xffff and (ref>=len(names) or names[ref] is None) for ref in ref_ids):raise ValueError('Invalid GRP referenced name ID')
        dependencies.append({'ordinal':i,'name_id':name_id,'name':names[name_id],'class_id':cls,
           'reference_offset':refs_at,'reference_ids':ref_ids,
           'reference_names':[None if ref==0xffff else names[ref] for ref in ref_ids],'reserved':reserved_fields})
    return {'version':version,'desc_only_size':desc,'full_data_size':full,'full_pack_size':rest+16,
            'names_count':names_count,'resource_count':entries_count,'dependency_count':deps_count,
            'names':names,'entries':entries,'dependencies':dependencies}
