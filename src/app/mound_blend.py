"""Continuous mound profiles with the existing ring-volume quadrature.

Static column geometry is shared across phases. Only 65 offsets are blended;
the two precomputed inverses seed a small exact volume correction. No full
capacity table is sorted or rebuilt in the animation loop.
"""
import math

try:
    import numpy as np
except ImportError:
    np=None


class VolumeColumns:
    def __init__(self,radius,xs):
        bottom=[];top=[];weights=[]
        for i,x in enumerate(xs):
            left=(xs[i-1]+x)*.5 if i else -radius
            right=(x+xs[i+1])*.5 if i+1<len(xs) else radius
            half=math.sqrt(max(0.,radius*radius-x*x))
            bottom.append(radius-half);top.append(radius+half)
            weights.append((right*abs(right)-left*abs(left))*.5)
        self.bottom=bottom;self.top=top;self.weights=weights
        if np is not None:
            self.bottom=np.asarray(bottom);self.top=np.asarray(top);self.weights=np.asarray(weights)


class BlendedArea:
    def __init__(self,low,high,weight,shape,columns):
        self.low,self.high,self.weight=low.heap,high.heap,weight
        self.capacity=low.heap.capacity
        offsets=[]
        for i,value in enumerate(shape):
            offsets.append(value)
            if i+1<len(shape):offsets.append((value+shape[i+1])*.5)
        self.weights=columns.weights
        if np is not None:
            offsets=np.asarray(offsets)
            self.starts=columns.bottom-offsets;self.ends=columns.top-offsets
        else:
            self.starts=[a-b for a,b in zip(columns.bottom,offsets)]
            self.ends=[a-b for a,b in zip(columns.top,offsets)]
        self.minimum=float(min(self.starts));self.maximum=float(max(self.ends))
        self.iterations=0

    def _value_slope(self,height):
        if np is not None:
            depth=np.maximum(0.,np.minimum(height,self.ends)-self.starts)
            value=float(np.dot(self.weights,depth))
            active=(height>self.starts)&(height<self.ends)
            return value,float(self.weights[active].sum())
        value=slope=0.
        for start,end,weight in zip(self.starts,self.ends,self.weights):
            value+=weight*max(0.,min(height,end)-start)
            if start<height<end:slope+=weight
        return value,slope

    def area_at(self,height):
        return max(0.,min(self.capacity,self._value_slope(height)[0]))

    def height_at(self,area):
        if area<=0.:return self.minimum
        if area>=self.capacity:return self.maximum
        a=self.low.height_at(area);b=self.high.height_at(area)
        height=a+(b-a)*self.weight
        lo,hi=self.minimum,self.maximum
        tolerance=max(1e-12,self.capacity*1e-11)
        for iteration in range(48):
            value,slope=self._value_slope(height)
            self.iterations=iteration+1
            error=value-area
            if abs(error)<=tolerance:return height
            if error>0.:hi=height
            else:lo=height
            candidate=height-error/slope if slope>0. else (lo+hi)*.5
            height=candidate if lo<candidate<hi else (lo+hi)*.5
        raise ArithmeticError('Continuous mound volume inverse did not converge')


def blend_profile(low,high,weight,columns):
    if weight<=0.:return low
    if weight>=1.:return high
    # The original profile methods continue to own every contact/render path.
    result=object.__new__(type(low))
    result.radius=low.radius
    result.shape=tuple(a+(b-a)*weight for a,b in zip(low.shape,high.shape))
    result.xs=low.xs;result.flat=low.flat
    result.slope_l=abs(result.shape[0])/result.radius
    result.slope_r=abs(result.shape[-1])/result.radius
    result.heap=BlendedArea(low,high,weight,result.shape,columns)
    result._ctab_axes=None;result._geom_cache={}
    return result
