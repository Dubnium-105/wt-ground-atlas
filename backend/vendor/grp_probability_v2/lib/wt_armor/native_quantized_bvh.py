"""Pinned float32 quantized BVH slab admission, including source box padding.

143111310 transforms the ray into quantized coordinates. 143140c20 expands
integer bounds by minus/plus one before testing them. Reconstructing an
unexpanded world-space box is not the same operation at grazing boundaries.
"""
import struct
import numpy as np
from .native_bvh_reference import leaf_boxes

f=np.float32


def quantized_leaf_boxes(chunk):
    if '_quantized_slab_boxes' in chunk:return chunk['_quantized_slab_boxes']
    raw=chunk['prepared_bytes'];size,root=struct.unpack_from('<II',raw,36);payload=raw[48:];boxes=[]
    def node(ref):
        count=(ref&3)+1;offset=ref&~3
        for i in range(count):
            low=[struct.unpack_from('<H',payload,offset+(axis*count+i)*2)[0] for axis in range(3)]
            high=[struct.unpack_from('<H',payload,offset+((axis+3)*count+i)*2)[0] for axis in range(3)]
            word=struct.unpack_from('<I',payload,offset+12*count+i*4)[0]
            if word&0x80000000:boxes.append((low,high))
            else:node(word)
    if size==20:boxes=None
    else:node(root)
    _,ids=leaf_boxes(chunk)
    if boxes is not None and len(boxes)!=len(ids):raise ValueError('Quantized leaf ordering differs')
    result=(None if boxes is None else np.asarray(boxes,dtype=np.float32),ids)
    chunk['_quantized_slab_boxes']=result
    return result


def admitted_face_indices(chunk,origin,direction,max_distance,constants):
    boxes,ids=quantized_leaf_boxes(chunk)
    if boxes is None:return {i for group in ids for i in group}
    frame=np.asarray(struct.unpack_from('<9f',chunk['prepared_bytes']),dtype=np.float32)
    origin,direction=np.asarray(origin,dtype=np.float32),np.asarray(direction,dtype=np.float32)
    scaled=f(direction*frame[:3]);inverse=np.full(3,f(constants['parallel_reciprocal']),dtype=np.float32)
    np.divide(f(1.),scaled,out=inverse,where=np.abs(scaled)>f(constants['scaled_direction_epsilon']))
    offset=f(f(f(frame[6:9]-origin)*frame[:3])*inverse)
    low=f(f(f(boxes[:,0]+f(constants['quantized_low_padding']))*inverse)+offset)
    high=f(f(f(boxes[:,1]+f(constants['quantized_high_padding']))*inverse)+offset)
    # SSE MINPS/MAXPS select the second operand on equality or unordered.
    minimum=lambda a,b:np.where(a<b,a,b).astype(np.float32)
    maximum=lambda a,b:np.where(a>b,a,b).astype(np.float32)
    lo,hi=minimum(low,high),maximum(low,high)
    entry=maximum(maximum(lo[:,0],lo[:,1]),maximum(lo[:,2],f(0.)))
    exit_=minimum(minimum(hi[:,0],hi[:,1]),minimum(hi[:,2],f(max_distance)))
    return {face for leaf in np.flatnonzero(entry<=exit_) for face in ids[leaf]}
