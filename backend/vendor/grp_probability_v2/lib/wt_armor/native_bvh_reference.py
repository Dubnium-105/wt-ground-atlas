"""Prepared BLAS leaf-box admission for the independent triangle reference.

The flat leaf test allows small negative barycentric numerators. A triangle
outside the ray's admitted BVH leaves must not be added by that tolerance.
This decodes the prepared SoA4 boxes and leaf references without executing
the original query. It does not claim byte parity of the resource loader.
"""
import struct
import numpy as np
from .native_blas_chunk import leaf_faces

def leaf_boxes(chunk):
    if '_reference_leaf_boxes' in chunk:return chunk['_reference_leaf_boxes']
    raw=chunk['prepared_bytes'];frame=struct.unpack_from('<9f',raw);tree_bytes,root_ref=struct.unpack_from('<II',raw,36)
    payload=raw[48:];vertices_offset=(tree_bytes+7)&~7
    lookup={}
    for i,face in enumerate(chunk['faces']):lookup.setdefault(tuple(map(int,face)),[]).append(i)
    boxes=[];indices=[]
    def leaf(w0,body,box):
        w1,w2,w3=struct.unpack_from('<III',payload,body)
        target=body+(w1&0xffffff)*4
        if (target-vertices_offset)%8:raise ValueError('Prepared BVH vertex reference is unaligned')
        base=(target-vertices_offset)//8
        faces=leaf_faces([w0,(w1&0xff000000)|base,w2,w3])
        if base<0 or any(tuple(face) not in lookup for face in faces):raise ValueError('Prepared BVH face identity differs')
        ids=[j for face in faces for j in lookup[tuple(face)]]
        if box is None:
            boxes.append(([-np.inf]*3,[np.inf]*3))
        else:
            low,high=box;s=np.asarray(frame[3:6],dtype=np.float32);t=np.asarray(frame[6:9],dtype=np.float32)
            a=np.float32(np.float32(low)*s+t);b=np.float32(np.float32(high)*s+t)
            boxes.append((np.minimum(a,b).astype(float).tolist(),np.maximum(a,b).astype(float).tolist()))
        indices.append(ids)
    def node(ref):
        count=(ref&3)+1;offset=ref&~3;body=offset+16*count
        for i in range(count):
            low=[struct.unpack_from('<H',payload,offset+(axis*count+i)*2)[0] for axis in range(3)]
            high=[struct.unpack_from('<H',payload,offset+((axis+3)*count+i)*2)[0] for axis in range(3)]
            word=struct.unpack_from('<I',payload,offset+12*count+i*4)[0]
            if word&0x80000000:leaf(word,body,(low,high));body+=12
            else:node(word)
    if tree_bytes==20:leaf(struct.unpack_from('<I',payload)[0],4,None)
    else:node(root_ref)
    result=(np.asarray(boxes,dtype=float),indices);chunk['_reference_leaf_boxes']=result
    return result

def admitted_face_indices(chunk,origin,direction,max_distance):
    boxes,indices=leaf_boxes(chunk);o=np.asarray(origin,dtype=float);d=np.asarray(direction,dtype=float)
    enter=np.full(len(boxes),-np.inf);exit_=np.full(len(boxes),np.inf);valid=np.ones(len(boxes),dtype=bool)
    for k in range(3):
        if d[k]==0.:
            valid &= (o[k]>=boxes[:,0,k]) & (o[k]<=boxes[:,1,k])
        else:
            a=(boxes[:,0,k]-o[k])/d[k];b=(boxes[:,1,k]-o[k])/d[k]
            enter=np.maximum(enter,np.minimum(a,b));exit_=np.minimum(exit_,np.maximum(a,b))
    valid &= (enter<=exit_) & (exit_>=0.) & (enter<float(max_distance))
    return {face for i in np.flatnonzero(valid) for face in indices[i]}
