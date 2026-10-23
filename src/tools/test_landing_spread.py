"""Surface grains must stay on the actual slope without jumping through a growing pile."""
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from splash_motion import step


def particle(*, scalar=False, x=10.0, speed=30.0, rest=.01, rise=0.0, dt=1/60):
    surface=lambda xx:100.0-.6*abs(xx)+rise
    w=SimpleNamespace(_sn=1, _cx=0.0,
        _splash_limits=(4.0,7.0,.5,.28,0.0), _splash_stats=defaultdict(int),
        _mound_top_at=surface, _contact_flow_diameter=lambda:40.0)
    values=dict(sx=x,sy=100-.6*abs(x),svx=speed,svy=0.0,sgd=1.0,
        srest=rest,sstill=0.0,shas=0.0,sdt=dt,saway=0.0,
        shw=.5,shh=.5,srw=.5,srh=.5)
    for key,value in values.items():setattr(w,key,[value] if scalar else np.array([value],float))
    table=[surface(xx) for xx in range(-100,101)]
    return w,table


def advance(w,table,scalar=False):
    return step(w,table,100.0,1.0,0.0,100.0,450.0,1.0,1.0,None,scalar)


class LandingSpreadTests(unittest.TestCase):
    def test_contacted_grain_follows_downhill_surface_and_counts_contact_time(self):
        for scalar in (False,True):
            w,table=particle(scalar=scalar)
            for _ in range(5):
                self.assertTrue(advance(w,table,scalar)[0])
                self.assertAlmostEqual(w.sy[0],w._mound_top_at(w.sx[0]),places=6)
            self.assertAlmostEqual(w.srest[0],.01+5/60,places=6)
            self.assertGreater(w.sx[0],10.0)

    def test_buried_surface_grain_is_not_lifted_back_to_the_new_top(self):
        for scalar in (False,True):
            w,table=particle(scalar=scalar,rise=8.0)
            old_y=w.sy[0]
            self.assertTrue(advance(w,table,scalar)[0])
            self.assertEqual(w.sy[0],old_y)
            self.assertEqual(w.svx[0],0.0)
            self.assertEqual(w.shas[0],1.0)

    def test_zero_dt_preserves_position_velocity_and_ages(self):
        for scalar in (False,True):
            w,table=particle(scalar=scalar,rise=8.0,dt=0.0)
            before={key:list(getattr(w,key)) for key in ('sx','sy','svx','svy','srest','sstill','shas')}
            self.assertTrue(advance(w,table,scalar)[0])
            for key,value in before.items():self.assertEqual(list(getattr(w,key)),value)

    def test_scalar_and_vector_surface_steps_agree(self):
        a,table=particle()
        b,_=particle(scalar=True)
        for _ in range(18):
            self.assertEqual(bool(advance(a,table)[0]),bool(advance(b,table,True)[0]))
            for key in ('sx','sy','svx','svy','srest','sstill','shas'):
                np.testing.assert_allclose(getattr(a,key),getattr(b,key),atol=1e-10)

    def test_airborne_grain_keeps_its_original_ballistic_motion(self):
        for scalar in (False,True):
            w,table=particle(scalar=scalar,rest=0.0)
            w.sy[0]=130.0;w.svy[0]=20.0
            self.assertTrue(advance(w,table,scalar)[0])
            self.assertAlmostEqual(w.sx[0],10+30/60)
            self.assertAlmostEqual(w.sy[0],130+20/60-.5*450/60**2)
            self.assertAlmostEqual(w.svy[0],20-450/60)
            self.assertEqual(w.srest[0],0.0)

    def test_receding_surface_releases_grain_without_a_downward_jump(self):
        for scalar in (False,True):
            w,table=particle(scalar=scalar,rise=-8.0)
            self.assertTrue(advance(w,table,scalar)[0])
            self.assertAlmostEqual(w.sy[0],94.0-.5*450/60**2)
            self.assertAlmostEqual(w.svy[0],-450/60)

    def test_left_and_right_spread_remain_local_and_end_without_new_grains(self):
        for scalar in (False,True):
            for side in (-1,1):
                w,table=particle(scalar=scalar,x=side*10,speed=side*90)
                start=w.sx[0]
                for frame in range(100):
                    if not advance(w,table,scalar)[0]:break
                    self.assertGreaterEqual(side*(w.sx[0]-start),0)
                    self.assertLess(abs(w.sx[0]-start),w._contact_flow_diameter())
                else:self.fail('A landed grain exceeded its existing finite contact/still budget')
                self.assertLess((frame+1)/60,.8)
                self.assertGreater(abs(w.sx[0]-start),2)
                self.assertEqual(w._sn,1)

    def test_buried_grain_uses_the_original_still_lifetime_and_stays_buried(self):
        for scalar in (False,True):
            w,table=particle(scalar=scalar,rise=8.0)
            before=w.sy[0]
            for frame in range(40):
                kept=advance(w,table,scalar)[0]
                self.assertEqual(w.sy[0],before)
                if not kept:break
            else:self.fail('Buried grain did not finish its existing still lifetime')
            self.assertGreaterEqual((frame+1)/60,.28)
            self.assertLess((frame+1)/60,.31)
            self.assertEqual(w._splash_stats['settled'],1)


if __name__=='__main__':unittest.main()
