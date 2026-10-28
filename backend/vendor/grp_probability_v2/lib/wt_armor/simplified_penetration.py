"""Explicit simplified armor-stack probability model, not native game parity.

Fixed straight path; sum selected layer costs; independent ricochet survival;
one uniform shot-level penetration dispersion. No fragmentation or lethality.
Geometry and ordered intervals are prepared once and reused for new ammunition.
"""
from pathlib import Path
import hashlib,json,time
import numpy as np
from .gpu_slope import CUDA as SLOPE_CODE

HELPER=SLOPE_CODE[:SLOPE_CODE.index('extern "C"')]
CODE=HELPER+r'''
__device__ float table(const float* k,const int* o,const float* x,const float* y,int nr,float r,float c){
 if(nr==0)return 0.0f;
 if(nr==1 || r<=k[0])return curve(x,y,o[0],o[1],c);
 if(r>=k[nr-1])return curve(x,y,o[nr-1],o[nr],c);
 int lo=0,hi=nr;while(lo<hi){int m=(lo+hi)/2;if(k[m]<r)lo=m+1;else hi=m;}
 int j=lo,i=j-1;float a=curve(x,y,o[i],o[i+1],c),b=curve(x,y,o[j],o[j+1],c);
 float w=__fmul_rn(__fsub_rn(r,k[i]),__fdiv_rn(1.0f,__fsub_rn(k[j],k[i])));
 return __fadd_rn(__fmul_rn(__fsub_rn(b,a),w),a);
}
__device__ float mn(float a,float b){return a<b?a:b;}
__device__ float mx(float a,float b){return a>b?a:b;}
extern "C" __global__ void probability(const int* offsets,const int* node,const float* entry,const float* exit_,
 const float* cosine,const float* extra,const int* kinds,const int* flags,const float* material,
 const float* sk,const int* so,const float* sx,const float* sy,int sn,
 const float* rk,const int* ro,const float* rx,const float* ry,int rn,
 int n,int mode,float penetration,float dispersion,float caliber,float air,float maxscale,
 float* output,float* required,unsigned char* state){
 int id=blockIdx.x*blockDim.x+threadIdx.x;if(id>=n)return;
 int begin=offsets[id],end=offsets[id+1];float target=__int_as_float(0x7f800000);
 output[id]=__int_as_float(0x7fffffff);required[id]=__int_as_float(0x7fffffff);
 if(begin==end){state[id]=0;return;}
 for(int i=begin;i<end;i++)if(kinds[node[i]]==2){target=entry[i];break;}
 if(!isfinite(target)){state[id]=1;return;}
 float cost=0.0f,survive=1.0f,covered=0.0f;bool started=false,bad=false;
 for(int i=begin;i<end;i++){
  if(entry[i]>=target)break;
  int ni=node[i],kind=kinds[ni];if(kind==0){bad=true;continue;}if(kind!=1)continue;
  if(extra[i]<target){bad=true;continue;}
  const float* m=material+ni*8;int fl=flags[ni];
  bool solid=(fl&1)!=0;
  if(solid && !isfinite(exit_[i])){bad=true;continue;}
  float t=m[0],q=m[1],c=cosine[i],base=mn(__fmul_rn(t,q),m[4]),normal=base,los=base;
  float last=entry[i];
  if(solid){float chord=__fmul_rn(__fsub_rn(exit_[i],entry[i]),1000.0f);
   los=mx(__fmul_rn(m[3],t),__fmul_rn(q,chord));normal=mn(mx(__fmul_rn(c,los),m[2]),m[4]);last=exit_[i];}
  else{float scale=mn(__fdiv_rn(1.0f,mx(c,1e-6f)),maxscale);los=mn(__fmul_rn(scale,base),m[5]);}
  if(mode==0 && sn && caliber>m[7] && normal>0.0f){
   float inv=table(sk,so,sx,sy,sn,__fdiv_rn(caliber,normal),c);inv=mx(mn(mx(inv,4e-19f),1.0f),1e-6f);
   los=mn(__fmul_rn(normal,mn(__fdiv_rn(1.0f,inv),maxscale)),m[5]);
  }
  float charge=normal>4e-19f?(solid?los:mx(los,m[6])):0.0f;
  if(mode==1 && started && entry[i]>covered)cost=__fadd_rn(cost,__fmul_rn(__fsub_rn(entry[i],covered),air));
  covered=started?mx(covered,last):last;started=true;cost=__fadd_rn(cost,charge);
  if((fl&2) && rn && normal>4e-19f){float ric=table(rk,ro,rx,ry,rn,__fdiv_rn(caliber,normal),c);
   ric=mn(mx(ric,0.0f),1.0f);survive=__fmul_rn(survive,__fsub_rn(1.0f,ric));}
 }
 if(bad){state[id]=2;return;}
 if(mode==1 && started && target>covered)cost=__fadd_rn(cost,__fmul_rn(__fsub_rn(target,covered),air));
 float variation=__fmul_rn(penetration,dispersion),p;
 if(variation>0.0f)p=mn(mx(__fdiv_rn(__fsub_rn(__fadd_rn(penetration,variation),cost),__fadd_rn(variation,variation)),0.0f),1.0f);
 else p=penetration>=cost?1.0f:0.0f;
 output[id]=__fmul_rn(p,survive);required[id]=cost;state[id]=3;
}
'''

COLOR=r'''
extern "C" __global__ void color(const float* p,const unsigned char* state,int n,unsigned char* rgb){
 int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=n)return;
 unsigned char* v=rgb+i*3;int status=state[i];
 if(status==0){v[0]=21;v[1]=30;v[2]=40;return;}
 if(status==1){v[0]=89;v[1]=103;v[2]=120;return;}
 if(status==2){v[0]=170;v[1]=85;v[2]=180;return;}
 double q=(double)p[i]*2.0;int a=q<1.0?0:1;double t=q-a;
 const double pal[9]={190,55,62,235,180,63,48,151,113};
 for(int k=0;k<3;k++)v[k]=(unsigned char)(pal[a*3+k]*(1.0-t)+pal[(a+1)*3+k]*t);
}
'''


class PreparedVehicle:
    def __init__(self,metadata):
        import cupy as cp
        self.cp=cp;metadata=Path(metadata);self.meta=json.loads(metadata.read_text(encoding='utf8'));self.plan=self.meta['plan']
        packet=metadata.with_suffix('.npz')
        if hashlib.sha256(packet.read_bytes()).hexdigest()!=self.meta['arrays_sha256']:raise ValueError('Layer cache changed')
        with np.load(packet) as z:arrays={k:z[k] for k in z.files}
        order=np.lexsort((arrays['entry_m'],arrays['ray_id']));self.n=self.plan['width']*self.plan['height']*len(self.plan['angles_deg'])
        offsets=np.r_[0,np.cumsum(np.bincount(arrays['ray_id'],minlength=self.n),dtype=np.int64)].astype('i4')
        self.data=[cp.asarray(offsets)]+[cp.asarray(np.ascontiguousarray(arrays[k][order])) for k in ['node','entry_m','exit_m','incidence_cos','extra_entry_m']]
        self.materials=self.meta['materials'];self.kernel=cp.RawKernel(CODE,'probability',options=('--fmad=false','--ftz=false','--prec-div=true'))
        self.outputs=[cp.empty(self.n,cp.float32),cp.empty(self.n,cp.float32),cp.empty(self.n,cp.uint8)]
        self.rgb=cp.empty((self.n,3),cp.uint8);self.color_kernel=cp.RawKernel(COLOR,'color',options=('--fmad=false',))
        self.material_cache={};self.table_cache={}

    def prepare_materials(self,ammo):
        cp=self.cp;mechanism=ammo['mechanism_id'];key=(ammo['mode'],mechanism)
        if key in self.material_cache:return self.material_cache[key]
        kinds=[];flags=[];values=[]
        for m in self.materials:
            cl=m['loaded_class'];cf=m['class_flags'];kind=0;fl=0;v=[0.,0.,0.,0.,1e9,1e9,0.,0.]
            # The audited pristine binding explicitly rejects node part=-1;
            # these are excluded collision nodes, not missing armor materials.
            if m['part_id']<0:
                kind=3
            elif cf is not None:
                if cf&4:kind=2
                elif not cf&2:kind=3
                elif cl is not None:
                    ar=cl['armor'];kind=1 if ar['enabled'] and ar['armorThickness']>0 else 3
                    if kind==1:
                        aq=ar['arrays'];index=mechanism
                        if any(index>=len(aq[k]['values']) for k in ['ArmorQuality','ThicknessEquivalentMax','EffectiveThicknessMax']):kind=0
                        else:
                            v=[ar['armorThickness'],float(np.float32(np.float32(ar['armorQuality'])*np.float32(aq['ArmorQuality']['values'][index]))),
                               ar['minSolidArmorThickness'],0.,aq['ThicknessEquivalentMax']['values'][index],aq['EffectiveThicknessMax']['values'][index],ar['armorThrough'],cl['scalars']['slopeMinCaliber']]
                            fl=int(bool(m['behavior_flags']&4) and not ar['ignoreSolidDimension'])
                            fl|=2 if m['decoded_flags']['allowRicochet'] else 0
            kinds.append(kind);flags.append(fl);values.append(v)
        result=[cp.asarray(np.asarray(a,dtype)) for a,dtype in [(kinds,'i4'),(flags,'i4'),(values,'f4')]]
        self.material_cache[key]=result;return result

    def prepare_table(self,rows):
        cp=self.cp;key=json.dumps(rows)
        if key in self.table_cache:return self.table_cache[key]
        knots=[];offset=[0];xs=[];ys=[]
        for ratio,points in rows:
            knots.append(ratio)
            for x,y in points:xs.append(x);ys.append(y)
            offset.append(len(xs))
        result=[cp.asarray(np.asarray(a,dtype)) for a,dtype in [(knots,'f4'),(offset,'i4'),(xs,'f4'),(ys,'f4')]]+[np.int32(len(rows))]
        self.table_cache[key]=result;return result

    def calculate(self,ammo,return_host=True):
        cp=self.cp
        for k in ['penetration_mm','dispersion','damage_caliber_mm','air_loss_mm_per_m']:
            if not np.isfinite(ammo[k]) or ammo[k]<0:raise ValueError('Invalid '+k)
        if ammo['mode'] not in ['KE','CE'] or not 0<=ammo['dispersion']<1:raise ValueError('Unsupported mode/dispersion')
        mat=self.prepare_materials(ammo);s=self.prepare_table(ammo['slope_rows']);r=self.prepare_table(ammo['ricochet_rows'])
        self.kernel(((self.n+255)//256,),(256,),(*self.data,*mat,*s,*r,np.int32(self.n),np.int32(ammo['mode']=='CE'),
           np.float32(ammo['penetration_mm']),np.float32(ammo['dispersion']),np.float32(ammo['damage_caliber_mm']),np.float32(ammo['air_loss_mm_per_m']),np.float32(20.),*self.outputs))
        if return_host:return [cp.asnumpy(a).reshape(len(self.plan['angles_deg']),self.plan['height'],self.plan['width']) for a in self.outputs]
        return self.outputs

    def calculate_rgb(self,ammo):
        self.calculate(ammo,return_host=False)
        self.color_kernel(((self.n+255)//256,),(256,),(self.outputs[0],self.outputs[2],np.int32(self.n),self.rgb))
        return self.cp.asnumpy(self.rgb).reshape(len(self.plan['angles_deg']),self.plan['height'],self.plan['width'],3)
