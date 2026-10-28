"""Pinned leaf ray arithmetic, with the original SSE dot-product order.

14313fbb0 forms each dot product as (Y product + X product) + Z product.
Near-parallel rays can magnify a one-ULP determinant change. No ray distance,
normal or barycentric acceptance tolerance is widened by this implementation.
"""
import struct
import numpy as np
from .native_loaded_bvh import decode_runtime_leaves
from .native_blas_chunk import signed13


def triangle_hits(vertices,faces,origin,direction,max_distance,both,minimum_barycentric_numerator,maximum_barycentric_sum,
                  second_triangle_flags=None,raw_scaled_vertices=None,translation=None):
    f=np.float32
    triangles=np.asarray(vertices,dtype=np.float32)[faces]
    origin,direction=np.asarray(origin,dtype=np.float32),np.asarray(direction,dtype=np.float32)
    p=f(triangles[:,0]-origin)
    r,e=f(triangles[:,0]-triangles[:,1]),f(triangles[:,2]-triangles[:,0])
    if second_triangle_flags is not None:
        mask=np.asarray(second_triangle_flags,dtype=bool)
        # Original 1431408bb..8be: scaled raw C + (translation - A).
        e[mask]=f(raw_scaled_vertices[np.asarray(faces)[mask,2]]+f(translation-triangles[mask,0]))
    def cross(a,b):
        return np.column_stack([f(f(a[:,2]*b[:,1])-f(a[:,1]*b[:,2])),
            f(f(a[:,0]*b[:,2])-f(a[:,2]*b[:,0])),f(f(a[:,1]*b[:,0])-f(a[:,0]*b[:,1]))])
    def dot(a,b):
        return f(f(f(a[:,1]*b[:,1])+f(a[:,0]*b[:,0]))+f(a[:,2]*b[:,2]))
    normal_test=cross(e,r)
    q=cross(p,np.broadcast_to(direction,p.shape))
    det=dot(np.broadcast_to(direction,p.shape),normal_test)
    sign=np.where(np.signbit(det)&both,np.uint32(0x80000000),np.uint32(0))
    det=(det.view(np.uint32)^sign).view(np.float32)
    u=(dot(e,q).view(np.uint32)^sign).view(np.float32)
    v=(dot(r,q).view(np.uint32)^sign).view(np.float32)
    tn=(dot(p,normal_test).view(np.uint32)^sign).view(np.float32)
    with np.errstate(divide='ignore',invalid='ignore'):
        inverse=f(f(1.)/det);distance=f(tn*inverse);total=f(f(u+v)*inverse)
    valid=((det>f(0.))&(distance>=f(0.))&(distance<f(max_distance))
        &(u>=f(minimum_barycentric_numerator))&(v>=f(minimum_barycentric_numerator))&(total<=f(maximum_barycentric_sum)))
    normal=cross(e,f(triangles[:,1]-triangles[:,0]))
    return [{'face_index':int(i),'normal_unnormalized':normal[i].astype(float).tolist(),
        'distance_m':float(distance[i]),'barycentric_numerators':[float(u[i]),float(v[i])],
        'barycentric_sum':float(total[i])} for i in np.flatnonzero(valid)]


def chunk_hits(chunk,origin,direction,max_distance,both,minimum_barycentric_numerator,maximum_barycentric_sum):
    if '_original_ray_vertices' not in chunk:
        raw=chunk['prepared_bytes'];tree_bytes=struct.unpack_from('<I',raw,36)[0]
        vertex_start=48+((tree_bytes+7)&~7);frame=struct.unpack_from('<9f',raw)
        scale=np.float32(np.asarray(frame[3:6],dtype=np.float32)*np.float32(.03125))
        translation=np.asarray(frame[6:9],dtype=np.float32)
        words=struct.unpack_from('<'+'Q'*len(chunk['vertices']),raw,vertex_start)
        integers=np.asarray([[w&0x1fffff,(w>>21)&0x1fffff,w>>42] for w in words],dtype=np.float32)
        scaled=np.float32(integers*scale);vertices=np.float32(scaled+translation)
        second=[];payload=raw[48:]
        for leaf in decode_runtime_leaves(chunk):
            count=len(leaf['faces'])
            if leaf['short']:
                b_count=0
            else:
                w2,w3=struct.unpack_from('<II',payload,leaf['body_offset']+4)
                a,b,c=signed13(w2>>16),signed13(w3),signed13(w3>>13)
                b_count=(1+int(c!=b)) if a!=b else 0
            a_count=count-b_count
            if a_count not in [1,2] or b_count not in [0,1,2]:raise ValueError('Original quad sub-triangle count differs')
            flags=[False]*count
            if a_count==2:flags[1]=True
            if b_count==2:flags[-1]=True
            second.extend(flags)
        chunk['_original_ray_vertices']=(vertices,scaled,translation,second)
    vertices,scaled,translation,second=chunk['_original_ray_vertices']
    return triangle_hits(vertices,chunk['faces'],origin,direction,max_distance,both,
        minimum_barycentric_numerator,maximum_barycentric_sum,second,scaled,translation)
