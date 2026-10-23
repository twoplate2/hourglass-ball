"""Near-full receiving activity must remain local and settle after supply."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import grain_style


class ReceivingSurfaceTests(unittest.TestCase):
    def test_closed_surface_retains_small_local_activity(self):
        depth,dx,dy=grain_style.receiving_surface_profile(5,20,50,.1,0)
        self.assertGreater(depth,0)
        self.assertGreater(dx,0)
        self.assertLessEqual(depth,1.6)
        self.assertLessEqual(abs(dx),2.8)  # confined motion <= half the free-surface budget

    def test_far_or_empty_surface_has_no_phantom_activity(self):
        for x,thick in [(40,50),(41,50),(0,0)]:
            self.assertEqual(grain_style.receiving_surface_profile(x,20,thick,0,0),(0,0,0))

    def test_normal_free_surface_keeps_existing_motion(self):
        self.assertEqual(grain_style.receiving_surface_profile(5,20,50,.1,20),
                         grain_style.contact_surface_profile(5,20,50,.1))

    def test_crossing_roof_has_no_step(self):
        a=grain_style.receiving_surface_profile(15,50,100,0,.0001)
        b=grain_style.receiving_surface_profile(15,50,100,0,0)
        self.assertLess(max(abs(x-y) for x,y in zip(a,b)),1e-6)


if __name__=='__main__':unittest.main()
