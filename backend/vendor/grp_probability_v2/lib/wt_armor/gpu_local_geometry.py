"""Exact-order float32 local leaf intersection and quantized box admission.

This covers the retained local-query contract, not scene collection, edge
reference bits, world transforms, part binding, or physical damage response.
"""
import struct
import numpy as np
from .native_loaded_ray import chunk_hits
from .native_quantized_bvh import quantized_leaf_boxes

CODE=r'''
__device__ float add(float a,float b){return __fadd_rn(a,b);}
__device__ float sub(float a,float b){return __fsub_rn(a,b);}
__device__ float mul(float a,float b){return __fmul_rn(a,b);}
__device__ float mn(float a,float b){return a<b?a:b;}
__device__ float mx(float a,float b){return a>b?a:b;}
__device__ float dot(const float* a,const float* b){return add(add(mul(a[1],b[1]),mul(a[0],b[0])),mul(a[2],b[2]));}
__device__ void cross(const float* a,const float* b,float* c){
 c[0]=sub(mul(a[2],b[1]),mul(a[1],b[2]));
 c[1]=sub(mul(a[0],b[2]),mul(a[2],b[0]));
 c[2]=sub(mul(a[1],b[0]),mul(a[0],b[1]));}
extern "C" __global__ void query(const float* tri,const float* boxes,const float* frame,
 const float* rays,int nf,int nq,int boxed,float eps,float parallel,float lowpad,float highpad,
 float minbary,float maxbary,float* out){
 int i=blockIdx.x*blockDim.x+threadIdx.x;
 if(i>=nf*nq)return;
 int fi=i%nf,qi=i/nf;const float* t=tri+fi*15;const float* ray=rays+qi*8;
 out[i]=__int_as_float(0x7f800000);
 if(boxed){
  float lo[3],hi[3];
  for(int k=0;k<3;k++){
   float scaled=mul(ray[3+k],frame[k]);
   float inv=fabsf(scaled)>eps?__fdiv_rn(1.0f,scaled):parallel;
   float off=mul(mul(sub(frame[6+k],ray[k]),frame[k]),inv);
   float a=add(mul(add(boxes[fi*6+k],lowpad),inv),off);
   float b=add(mul(add(boxes[fi*6+3+k],highpad),inv),off);
   lo[k]=mn(a,b);hi[k]=mx(a,b);
  }
  float entry=mx(mx(lo[0],lo[1]),mx(lo[2],0.0f));
  float exit=mn(mn(hi[0],hi[1]),mn(hi[2],ray[6]));
  if(!(entry<=exit))return;
 }
 float p[3],q[3];for(int k=0;k<3;k++)p[k]=sub(t[k],ray[k]);
 cross(p,ray+3,q);
 float det=dot(ray+3,t+9);
 unsigned sign=(ray[7]!=0.0f && (__float_as_uint(det)&0x80000000u))?0x80000000u:0;
 det=__uint_as_float(__float_as_uint(det)^sign);
 float u=__uint_as_float(__float_as_uint(dot(t+6,q))^sign);
 float v=__uint_as_float(__float_as_uint(dot(t+3,q))^sign);
 float tn=__uint_as_float(__float_as_uint(dot(p,t+9))^sign);
 float inv=__fdiv_rn(1.0f,det),distance=mul(tn,inv),total=mul(add(u,v),inv);
 if(det>0.0f && distance>=0.0f && distance<ray[6] && u>=minbary && v>=minbary && total<=maxbary)out[i]=distance;
}
'''


class GpuLocalMesh:
    def __init__(self,chunk,constants):
        import cupy as cp
        self.cp=cp;self.constants=constants
        chunk_hits(chunk,[0.,0.,0.],[1.,0.,0.],0.,True,-4e-6,1.0000040531158447)
        vertices,scaled,translation,second=chunk['_original_ray_vertices']
        triangles=vertices[chunk['faces']];r=np.float32(triangles[:,0]-triangles[:,1]);e=np.float32(triangles[:,2]-triangles[:,0])
        mask=np.asarray(second,bool)
        e[mask]=np.float32(scaled[np.asarray(chunk['faces'])[mask,2]]+np.float32(translation-triangles[mask,0]))
        def cross(a,b):
            f=np.float32
            return np.column_stack([f(f(a[:,2]*b[:,1])-f(a[:,1]*b[:,2])),f(f(a[:,0]*b[:,2])-f(a[:,2]*b[:,0])),f(f(a[:,1]*b[:,0])-f(a[:,0]*b[:,1]))])
        nt=cross(e,r);self.normals=cross(e,np.float32(triangles[:,1]-triangles[:,0]))
        self.nf=len(triangles);self.tri=cp.asarray(np.column_stack([triangles[:,0],r,e,nt,self.normals]).astype('f4'))
        boxes,ids=quantized_leaf_boxes(chunk);self.boxed=int(boxes is not None)
        face_boxes=np.zeros((self.nf,6),np.float32)
        if boxes is not None:
            assigned=np.zeros(self.nf,np.uint8)
            for box,faces in zip(boxes,ids):
                face_boxes[faces]=box.reshape(6);assigned[faces]+=1
            if not np.all(assigned==1):raise ValueError('Every face must have exactly one leaf box')
        self.boxes=cp.asarray(face_boxes);self.frame=cp.asarray(np.asarray(struct.unpack_from('<9f',chunk['prepared_bytes']),np.float32))
        self.kernel=cp.RawKernel(CODE,'query',options=('--fmad=false','--ftz=false','--prec-div=true'))

    def distances(self,queries):
        cp=self.cp
        packed=np.asarray([q['origin']+q['direction']+[q['max_distance'],float(q['force_both'])] for q in queries],np.float32)
        if not np.isfinite(packed).all():raise ValueError('Non-finite query')
        nq=len(queries);out=cp.empty((nq,self.nf),cp.float32);args=self.constants
        if nq and self.nf:
            self.kernel(((nq*self.nf+255)//256,),(256,),
                (self.tri,self.boxes,self.frame,cp.asarray(packed),np.int32(self.nf),np.int32(nq),np.int32(self.boxed),
                 *[np.float32(args[k]) for k in ['scaled_direction_epsilon','parallel_reciprocal','quantized_low_padding','quantized_high_padding']],
                 np.float32(-3.999999989900971e-6),np.float32(1.0000040531158447),out))
        return cp.asnumpy(out)
