"""Interval-preserving armor-stack model with source KE range carry.

This remains a declared straight-path approximation: no yaw/precession,
finite-caliber grouping, ERA activation, or game probability calibration.
NERA coefficients come from source material fields; no guessed multiplier.
"""
import json
import numpy as np
from .simplified_penetration import PreparedVehicle as Previous,CODE as ORIGINAL_CODE

RANGE=r'''
__device__ float range_value(const float* xy,int count,float x){
 if(count==0)return 1.0f;
 if(x<=xy[0])return xy[1];if(x>=xy[(count-1)*2])return xy[(count-1)*2+1];
 int lo=0,hi=count;while(lo<hi){int m=(lo+hi)/2;if(xy[m*2]<x)lo=m+1;else hi=m;}
 int r=lo,l=r-1;float w=__fdiv_rn(__fsub_rn(x,xy[l*2]),__fsub_rn(xy[r*2],xy[l*2]));
 return __fadd_rn(xy[l*2+1],__fmul_rn(__fsub_rn(xy[r*2+1],xy[l*2+1]),w));
}
__device__ float range_inverse(const float* xy,int count,float y){
 if(y>=xy[1])return xy[0];if(y<=xy[(count-1)*2+1])return xy[(count-1)*2];
 int lo=0,hi=count;while(lo<hi){int m=(lo+hi)/2;if(xy[m*2+1]>y)lo=m+1;else hi=m;}
 int r=lo,l=r-1;float w=__fdiv_rn(__fsub_rn(y,xy[l*2+1]),__fsub_rn(xy[r*2+1],xy[l*2+1]));
 return __fadd_rn(xy[l*2],__fmul_rn(__fsub_rn(xy[r*2],xy[l*2]),w));
}
__device__ bool reaches(float initial,int begin,int end,const int* node,const int* kinds,const float* entry,
 const float* charges,float target,const float* xy,int count){
 float shift=0.0f;
 for(int i=begin;i<end && entry[i]<target;i++){
  if(kinds[node[i]]!=1 || charges[i]<=0.0f)continue;
  float available=__fmul_rn(initial,range_value(xy,count,__fadd_rn(entry[i],shift)));
  float remaining=__fsub_rn(available,charges[i]);if(remaining<=0.0f)return false;
  float next=__fsub_rn(range_inverse(xy,count,__fdiv_rn(remaining,initial)),entry[i]);if(next>shift)shift=next;
 }
 return true;
}
'''


def changed(source,old,new):
    if source.count(old)!=1:raise ValueError('Unexpected V1 template: '+old[:60])
    return source.replace(old,new,1)


CODE=ORIGINAL_CODE
CODE=changed(CODE,'extern "C" __global__ void probability',RANGE+'\nextern "C" __global__ void probability')
CODE=changed(CODE,'float* output,float* required,unsigned char* state)',
    'float* output,float* required,unsigned char* state,const float* distance_curve,int curve_count,float* layer_cost)')
CODE=changed(CODE,'bool solid=(fl&1)!=0;','bool solid=(fl&1)!=0;bool geometric=(fl&4)==0;')
CODE=changed(CODE,'else{float scale=mn(', 'else if(geometric){float scale=mn(')
CODE=changed(CODE,'if(mode==0 && sn && caliber>m[7] && normal>0.0f){',
    'bool thin=mode==0 && caliber>=__fmul_rn(7.0f,normal);if(thin)los=normal;\n  if(mode==0 && !thin && geometric && sn && caliber>m[7] && normal>0.0f){')
CODE=changed(CODE,'float charge=normal>4e-19f?(solid?los:mx(los,m[6])):0.0f;',
    'float charge=normal>4e-19f?(solid?los:mx(los,m[6])):0.0f;layer_cost[i]=charge;')
CODE=changed(CODE,'if(mode==1 && started && target>covered)cost=__fadd_rn(cost,__fmul_rn(__fsub_rn(target,covered),air));',
    '// Endpoint is the armor stack exit; room behind armor is not charged.')
CODE=changed(CODE,'output[id]=__fmul_rn(p,survive);required[id]=cost;state[id]=3;',r'''
 if(mode==0 && curve_count>1){
  float low=__fsub_rn(penetration,variation),high=__fadd_rn(penetration,variation);
  bool lp=reaches(low,begin,end,node,kinds,entry,layer_cost,target,distance_curve,curve_count);
  bool hp=reaches(high,begin,end,node,kinds,entry,layer_cost,target,distance_curve,curve_count);
  if(lp)p=1.0f;else if(!hp)p=0.0f;else if(variation<=0.0f)p=hp?1.0f:0.0f;
  else{float l=low,h=high;for(int step=0;step<16;step++){float m=__fmul_rn(__fadd_rn(l,h),0.5f);
    if(reaches(m,begin,end,node,kinds,entry,layer_cost,target,distance_curve,curve_count))h=m;else l=m;}
    p=__fdiv_rn(__fsub_rn(high,__fmul_rn(__fadd_rn(l,h),0.5f)),__fadd_rn(variation,variation));}
 }
 output[id]=__fmul_rn(p,survive);required[id]=cost;state[id]=3;
''')

RISK=r'''
extern "C" __global__ void families(const int* offsets,const int* node,const float* entry,
 const int* kinds,const unsigned char* node_family,int n,unsigned char* out){
 int id=blockIdx.x*blockDim.x+threadIdx.x;if(id>=n)return;
 int begin=offsets[id],end=offsets[id+1];float target=__int_as_float(0x7f800000);unsigned char flags=0;
 for(int i=begin;i<end;i++)if(kinds[node[i]]==2){target=entry[i];break;}
 if(isfinite(target))for(int i=begin;i<end && entry[i]<target;i++)if(kinds[node[i]]==1)flags|=node_family[node[i]];
 out[id]=flags;
}
'''


class PreparedVehicle(Previous):
    def __init__(self,metadata):
        super().__init__(metadata)
        if not self.meta.get('all_inward_node_intervals_retained'):raise ValueError('V2 requires full interval cache')
        self.kernel=self.cp.RawKernel(CODE,'probability',options=('--fmad=false','--ftz=false','--prec-div=true'))
        self.layer_cost=self.cp.empty(self.data[1].shape,self.cp.float32);self.range_cache={}
        self.risk_kernel=self.cp.RawKernel(RISK,'families');self.risk_cache=None
    def prepare_materials(self,ammo):
        result=super().prepare_materials(ammo);key=('v2_flags',ammo['mode'],ammo['mechanism_id'])
        if key not in self.material_cache:
            flags=self.cp.asnumpy(result[1])
            for i,m in enumerate(self.materials):
                if m['class_flags'] is not None and m['class_flags']&0x2000:
                    flags[i]=(flags[i]&~1)|4
            self.material_cache[key]=[result[0],self.cp.asarray(flags),result[2]]
        return self.material_cache[key]
    def calculate(self,ammo,return_host=True):
        cp=self.cp
        for k in ['penetration_mm','dispersion','damage_caliber_mm','air_loss_mm_per_m']:
            if not np.isfinite(ammo[k]) or ammo[k]<0:raise ValueError('Invalid '+k)
        if ammo['mode'] not in ['KE','CE'] or not 0<=ammo['dispersion']<1:raise ValueError('Unsupported mode/dispersion')
        rows=ammo.get('normalized_distance_curve',[]);key=json.dumps(rows)
        if key not in self.range_cache:
            values=np.asarray(rows,np.float32).reshape(-1,2)
            if len(values) and (not np.isfinite(values).all() or np.any(values[1:,0]<=values[:-1,0]) or np.any(values[1:,1]>values[:-1,1])):raise ValueError('Invalid monotone range curve')
            self.range_cache[key]=cp.asarray(values)
        distance=self.range_cache[key]
        mat=self.prepare_materials(ammo);s=self.prepare_table(ammo['slope_rows']);r=self.prepare_table(ammo['ricochet_rows'])
        self.kernel(((self.n+255)//256,),(256,),(*self.data,*mat,*s,*r,np.int32(self.n),np.int32(ammo['mode']=='CE'),
           np.float32(ammo['penetration_mm']),np.float32(ammo['dispersion']),np.float32(ammo['damage_caliber_mm']),np.float32(ammo['air_loss_mm_per_m']),np.float32(20.),*self.outputs,
           distance,np.int32(len(rows)),self.layer_cost))
        if return_host:return [cp.asnumpy(a).reshape(len(self.plan['angles_deg']),self.plan['height'],self.plan['width']) for a in self.outputs]
        return self.outputs

    def risk_flags(self,ammo):
        if self.risk_cache is None:
            family=[]
            for m in self.materials:
                sources=m.get('source_armor_classes',[]);flags=0
                if any('nera' in s.lower() for s in sources):flags|=1
                if m['decoded_flags'] and m['decoded_flags']['reactiveArmor']:flags|=2
                if 'spaced_armor_air' in sources or 'grille_air' in sources:flags|=4
                if m['decoded_flags'] and m['decoded_flags']['variableThickness']:flags|=8
                family.append(flags)
            out=self.cp.empty(self.n,self.cp.uint8);node_family=self.cp.asarray(np.asarray(family,np.uint8));kinds=self.prepare_materials(ammo)[0]
            self.risk_kernel(((self.n+255)//256,),(256,),(self.data[0],self.data[1],self.data[2],kinds,node_family,np.int32(self.n),out))
            self.risk_cache=self.cp.asnumpy(out).reshape(len(self.plan['angles_deg']),self.plan['height'],self.plan['width'])
        return self.risk_cache
