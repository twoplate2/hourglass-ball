import unittest
import numpy as np
from pixel_flow_review import match_shift


class PixelMotionTests(unittest.TestCase):
    def setUp(self):self.a=np.random.default_rng(713).normal(size=(80,24))
    def test_downward_shift(self):self.assertEqual(match_shift(self.a,np.roll(self.a,2,axis=0))[0],2)
    def test_frozen_detected(self):self.assertEqual(match_shift(self.a,self.a)[0],0)
    def test_reverse_detected(self):self.assertEqual(match_shift(self.a,np.roll(self.a,-2,axis=0))[0],-2)
    def test_subpixel_shift_is_not_rounded_to_an_unrelated_pattern(self):
        b=.5*np.roll(self.a,2,axis=0)+.5*np.roll(self.a,3,axis=0)
        self.assertAlmostEqual(match_shift(self.a,b)[0],2.5)


if __name__=='__main__':unittest.main()
