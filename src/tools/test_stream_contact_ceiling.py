"""Regression: empty-bulb sides are above the center's sand contact height."""
from pathlib import Path
import math,sys,unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import flow_numpy
from short_grain_stream import contact_upper_bound

class ContactTests(unittest.TestCase):
    def test_side_floor_is_hit_before_glass_clamp_teleports_grain(self):
        radius=208.84468421052628;center=222.90357421742667;cx=270.
        xs=np.linspace(cx-radius,cx+radius,129)
        ys=center-np.sqrt(np.maximum(0,radius*radius-(xs-cx)**2))
        curve=(xs.tolist(),ys.tolist(),float(xs[0]),128/(2*radius),128)
        c=dict(g=-450.,g_abs=450.,mound_top=center-radius,
            contact_ceiling=contact_upper_bound(center-radius,curve,cx,31.3267026),
            gen_y=446.7270283,lower_cut=446.7270283,lower_top=431.7482584,
            lower_center=center,tube_lim=31.3267026,Ri2=radius*radius,
            lower_bot=center-radius,source_speed=60.,source_speed_squared=3600.,
            shrink_min=.87,shrink_ramp=92.,shrink_sat=3.9820504,
            cx=cx,peak_offset=0.,sprite_pitch=1.8517562,curve=curve)
        px=np.array([293.20695]);py=np.array([19.95935]);vy=np.array([-621.60644])
        hits,_,_=flow_numpy.step(px,py,vy,np.array([26.]),np.array([6.1768658]),
            np.array([.5]),np.array([2]),np.array([1/120]),1,c)
        self.assertEqual(hits.tolist(),[0])
        self.assertGreater(py[0],center-radius)

    def test_gate_includes_local_bowl_floor_but_not_distant_walls(self):
        curve=([-100,-50,0,50,100],[100,10,0,10,100],-100,.02,4)
        self.assertEqual(contact_upper_bound(0,curve,0,25),10)
        self.assertEqual(contact_upper_bound(30,curve,0,25),30)

if __name__=='__main__':unittest.main()
