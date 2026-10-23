"""Separate known bad pictures; a nonempty image alone must not pass."""
import unittest

import numpy as np
from stream_review_metrics import inspect_frame,require_sequence


BG=(234,243,248)
SAND=(215,162,95)


def image(kind):
    a=np.full((240,100,3),BG,dtype=np.uint8)
    for y in range(15,225):
        left,right=40,60
        if kind=='wide':left,right=32,68
        if kind=='fine':
            left+=int((y//3)%3)-1;right+=int((y//5)%3)-1
        a[y,left:right]=SAND
        if kind=='fine':
            for x in range(left,right):
                if (x*17+y*13)%31<4:a[y,x]=BG
    if kind=='gap':a[100:113]=BG
    if kind=='blank':a[:]=BG
    return a


GEO=dict(cx=50,outlet_y=15,surface_y=225,half_width=10)


class PictureCounterexamples(unittest.TestCase):
    def test_fine_irregular_stream_passes(self):
        self.assertFalse(inspect_frame(image('fine'),GEO)['failures'])

    def test_exit_step_fails(self):
        self.assertIn('exit_expands',inspect_frame(image('wide'),GEO)['failures'])

    def test_equal_width_solid_rod_fails(self):
        self.assertIn('solid_rod',inspect_frame(image('rod'),GEO)['failures'])

    def test_empty_horizontal_layer_fails(self):
        self.assertIn('empty_layer',inspect_frame(image('gap'),GEO)['failures'])

    def test_invisible_stream_fails(self):
        self.assertIn('invisible',inspect_frame(image('blank'),GEO)['failures'])

    def test_single_bad_frame_cannot_hide_in_average(self):
        rows=[inspect_frame(image('fine'),GEO) for _ in range(30)]
        rows[12]=inspect_frame(image('wide'),GEO)
        with self.assertRaises(ValueError):require_sequence(rows)

    def test_whole_stream_sway_cannot_hide_in_average(self):
        rows=[inspect_frame(image('fine'),GEO) for _ in range(30)]
        for i,row in enumerate(rows):row['center_offset']=.25*20*np.sin(i/30*2*np.pi)
        with self.assertRaisesRegex(ValueError,'sway'):require_sequence(rows)

    def test_rough_edges_cannot_hide_solid_coarse_interior(self):
        rows=[inspect_frame(image('fine'),GEO) for _ in range(30)]
        for row in rows:row['core_coverage']=1.0
        with self.assertRaisesRegex(ValueError,'Coarse'):require_sequence(rows,coarse=True)


if __name__=='__main__':unittest.main()
