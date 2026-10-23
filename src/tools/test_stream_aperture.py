"""The visible grain footprint, not only its centre, must fit the aperture."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import short_grain_stream as stream
import neck_geometry as geometry


class ApertureTests(unittest.TestCase):
    def test_wider_stream_preserves_emission_density(self):
        self.assertGreaterEqual(stream.emission_scale(200,30),2)
        self.assertEqual(stream.emission_scale(200,3),1)

    def test_birth_footprint_fits_all_widths(self):
        for half in (3,5,12,25,60):
            for coarse in (1,3,6):
                pitch=stream.stream_pitch(170,coarse,half)
                radius=stream.sprite_radius(pitch,2)
                self.assertLessEqual(stream.source_half_width(half,pitch)+radius,half+1e-6)

    def test_source_support_reaches_aperture(self):
        # Collision clamping owns wobble safety. A second fixed margin here
        # leaves a visible shoulder when the dense neck hands over to grains.
        pitch=stream.stream_pitch(210,3,30)
        self.assertAlmostEqual(stream.source_half_width(30,pitch)+stream.sprite_radius(pitch,2),30)

    def test_long_period_footprint_fits_scaled_neck(self):
        for width,height in ((400,800),(400,875),(1080,1848),(1904,2890)):
            radius,wall=geometry.body_metrics(width,height)
            for period in (36000,360000):
                half=geometry.inner_width(period,2*(radius-wall))*.5
                for coarse in (1,3,6):
                    pitch=stream.stream_pitch(radius-wall,coarse,half)
                    support=stream.sprite_radius(pitch,2)
                    self.assertLessEqual(support,half)
                    self.assertAlmostEqual(stream.source_half_width(half,pitch)+support,half)

    def test_wider_aperture_does_not_enlarge_grains(self):
        self.assertEqual(stream.stream_pitch(210,3,20),stream.stream_pitch(210,3,40))


if __name__=='__main__':unittest.main()
