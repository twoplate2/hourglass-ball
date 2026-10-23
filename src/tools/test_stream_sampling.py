"""Birth coverage is bounded without adding grains or modifying live positions."""
import sys
from pathlib import Path
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from short_grain_stream import coherent_birth

class BirthTests(unittest.TestCase):
    def test_short_windows_cover_both_edges(self):
        values=[coherent_birth(i,.5,.5)[0] for i in range(4096)]
        for start in range(len(values)-24):
            window=values[start:start+24]
            self.assertLess(min(window),-.8)
            self.assertGreater(max(window),.8)

    def test_jitter_cannot_erase_coverage_or_exceed_aperture(self):
        for r in (0.,1.):
            values=[coherent_birth(i,r,r) for i in range(512)]
            self.assertTrue(all(-1<=x<=1 and 35<=v<=60 for x,v in values))
            self.assertTrue(all(47.25<=v<=47.75 for _,v in values[96:]))
            for start in range(480):
                self.assertLess(min(x for x,_ in values[start:start+24]),-.8)
                self.assertGreater(max(x for x,_ in values[start:start+24]),.8)

    def test_accelerated_lower_rows_preserve_distribution(self):
        rate=1500
        births=np.arange(1600)/rate
        x,v=np.array([coherent_birth(i,(i*.31)%1,(i*.71)%1) for i in range(1600)]).T
        age=1.1-births
        depth=v*age+225*age*age
        # A 12px cluster support at the lower bulb must not lose an entire edge.
        for y in np.arange(180,260,.5):
            row=x[np.abs(depth-y)<6]
            self.assertGreater(len(row),16)
            self.assertLess(row.min(),-.75)
            self.assertGreater(row.max(),.75)

    def test_deterministic_without_rng_or_time(self):
        self.assertEqual(coherent_birth(117,.2,.8),coherent_birth(117,.2,.8))
        self.assertNotEqual(coherent_birth(117,.2,.8),coherent_birth(118,.2,.8))

    def test_run_phase_preserves_short_window_coverage(self):
        for shift in (0,.03,.23,.5,.77,.99):
            values=[coherent_birth(i,.5,.5,shift)[0] for i in range(128)]
            for start in range(100):
                self.assertLess(min(values[start:start+24]),-.8)
                self.assertGreater(max(values[start:start+24]),.8)

    def test_start_front_keeps_speed_scatter_then_settles_smoothly(self):
        def span(index):return coherent_birth(index,.5,1)[1]-coherent_birth(index,.5,0)[1]
        self.assertGreaterEqual(span(0),24)
        self.assertAlmostEqual(span(96),.5)
        self.assertTrue(all(span(i)>=span(i+1) for i in range(96)))
        self.assertLess(max(abs(span(i+1)-span(i)) for i in range(96)),.6)

if __name__=='__main__':unittest.main()
