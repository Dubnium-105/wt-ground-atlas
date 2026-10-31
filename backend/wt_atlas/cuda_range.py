"""Explicit first-armor distance adapter for the unchanged vendored V2 kernel.

Only the range coordinate changes. Ordered geometry, material response and
probability integration remain V2; the adapter has a separate model identity.
"""
from pathlib import Path
import sys
import numpy as np


def range_kernel(source):
    def replace(old, new, count=1):
        nonlocal source
        if source.count(old) != count:
            raise ValueError('Unsupported V2 range-kernel template')
        source = source.replace(old, new)
    replace('const float* charges,float target,const float* xy,int count){',
            'const float* charges,float target,const float* xy,int count,float impact_distance){')
    replace('float shift=0.0f;\n for(int i=begin;', '''float shift=0.0f,first=0.0f;
 for(int j=begin;j<end && entry[j]<target;j++){
  if(kinds[node[j]]==1){first=entry[j];break;}
 }
 for(int i=begin;''')
    replace('float available=__fmul_rn(initial,range_value(xy,count,__fadd_rn(entry[i],shift)));',
            '''float coordinate=__fadd_rn(__fsub_rn(entry[i],first),impact_distance);
  float available=__fmul_rn(initial,range_value(xy,count,__fadd_rn(coordinate,shift)));''')
    replace('range_inverse(xy,count,__fdiv_rn(remaining,initial)),entry[i])',
            'range_inverse(xy,count,__fdiv_rn(remaining,initial)),coordinate)')
    replace('int curve_count,float* layer_cost)', 'int curve_count,float* layer_cost,float impact_distance)')
    replace('target,distance_curve,curve_count)', 'target,distance_curve,curve_count,impact_distance)', 3)
    return source


def prepare(metadata, vendor):
    vendor = Path(vendor).resolve()
    sys.path.insert(0, str(vendor/'lib'))
    from wt_armor.layered_penetration_v2 import PreparedVehicle, CODE

    class RangedVehicle(PreparedVehicle):
        def __init__(self, path):
            super().__init__(path)
            self.range_kernel = self.cp.RawKernel(range_kernel(CODE), 'probability',
                options=('--fmad=false', '--ftz=false', '--prec-div=true'))

        def calculate(self, ammo, return_host=True):
            distance = ammo.get('impact_distance_m')
            if distance is None:
                return super().calculate(ammo, return_host)
            curve = ammo.get('normalized_distance_curve', [])
            if (isinstance(distance, bool) or not isinstance(distance, (int, float)) or
                    not np.isfinite(distance) or ammo['mode'] != 'KE' or len(curve) < 2 or
                    not curve[0][0] <= distance <= curve[-1][0]):
                raise ValueError('Impact distance requires a supported KE source curve')
            # Retain V2's table preparation and validation; append the one new
            # coordinate argument. Prepared instances are local to one worker.
            legacy = self.kernel
            self.kernel = lambda grid, block, args: self.range_kernel(
                grid, block, (*args, np.float32(distance)))
            try:
                return super().calculate(ammo, return_host)
            finally:
                self.kernel = legacy

    return RangedVehicle(metadata)
