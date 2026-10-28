"""CUDA selected-record slope operator; no complete armor/damage claim.

Explicit round-to-nearest float32 operations disable FMA/reassociation and
approximate division. Caller supplies finite, validated source-table inputs.
"""
import numpy as np

CUDA = r'''
__device__ float curve(const float* x,const float* y,int start,int end,float v){
  if(start==end)return 0.0f;
  if(end-start==1 || v<=x[start])return y[start];
  if(v>=x[end-1])return y[end-1];
  int lo=start,hi=end;
  while(lo<hi){int m=(lo+hi)/2;if(x[m]<v)lo=m+1;else hi=m;}
  int r=lo,l=r-1;
  float inv=__fdiv_rn(1.0f,__fsub_rn(x[r],x[l]));
  float w=__fmul_rn(__fsub_rn(v,x[l]),inv);
  return __fadd_rn(__fmul_rn(__fsub_rn(y[r],y[l]),w),y[l]);
}
extern "C" __global__ void slope(const float* ratios,const float* cosines,
  const float* knots,const int* offsets,const float* x,const float* y,
  int nr,int n,float* out){
  int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=n)return;
  if(nr==0){out[i]=0.0f;return;}
  float v=ratios[i],c=cosines[i];
  if(nr==1 || v<=knots[0]){out[i]=curve(x,y,offsets[0],offsets[1],c);return;}
  if(v>=knots[nr-1]){out[i]=curve(x,y,offsets[nr-1],offsets[nr],c);return;}
  int lo=0,hi=nr;
  while(lo<hi){int m=(lo+hi)/2;if(knots[m]<v)lo=m+1;else hi=m;}
  int r=lo,l=r-1;
  float a=curve(x,y,offsets[l],offsets[l+1],c);
  float b=curve(x,y,offsets[r],offsets[r+1],c);
  float w=__fmul_rn(__fsub_rn(v,knots[l]),__fdiv_rn(1.0f,__fsub_rn(knots[r],knots[l])));
  out[i]=__fadd_rn(__fmul_rn(__fsub_rn(b,a),w),a);
}
'''


class GpuSlope:
    def __init__(self,rows):
        import cupy as cp
        self.cp=cp; self.nr=len(rows)
        knots=np.asarray([r[0] for r in rows],np.float32)
        if not np.isfinite(knots).all() or (len(knots)>1 and not np.all(knots[1:]>knots[:-1])):
            raise ValueError('Invalid slope ratio knots')
        xs=[];ys=[];offsets=[0]
        for _,points in rows:
            px=np.asarray([p[0] for p in points],np.float32)
            py=np.asarray([p[1] for p in points],np.float32)
            if not np.isfinite(px).all() or not np.isfinite(py).all() or (len(px)>1 and not np.all(px[1:]>px[:-1])):
                raise ValueError('Invalid slope points')
            xs.extend(px);ys.extend(py);offsets.append(len(xs))
        self.tables=[cp.asarray(a) for a in [knots,np.asarray(offsets,np.int32),np.asarray(xs,np.float32),np.asarray(ys,np.float32)]]
        self.kernel=cp.RawKernel(CUDA,'slope',options=('--fmad=false','--ftz=false','--prec-div=true','--prec-sqrt=true'))

    def device(self,ratio,cosine,out=None):
        cp=self.cp
        if ratio.dtype!=cp.float32 or cosine.dtype!=cp.float32 or ratio.shape!=cosine.shape:
            raise ValueError('Matching float32 device arrays required')
        if not ratio.flags.c_contiguous or not cosine.flags.c_contiguous:
            raise ValueError('Contiguous device arrays required')
        if ratio.size>2147483647: raise ValueError('Batch exceeds int32 indexing')
        if out is None:out=cp.empty_like(ratio)
        if out.shape!=ratio.shape or out.dtype!=cp.float32 or not out.flags.c_contiguous:
            raise ValueError('Invalid output buffer')
        if ratio.size:self.kernel(((ratio.size+255)//256,),(256,),(ratio,cosine,*self.tables,np.int32(self.nr),np.int32(ratio.size),out))
        return out

    def host(self,ratio,cosine):
        ratio,cosine=np.broadcast_arrays(np.asarray(ratio,np.float32),np.asarray(cosine,np.float32))
        if not np.isfinite(ratio).all() or not np.isfinite(cosine).all():raise ValueError('Non-finite input')
        return self.cp.asnumpy(self.device(self.cp.asarray(np.ascontiguousarray(ratio)),self.cp.asarray(np.ascontiguousarray(cosine))))
