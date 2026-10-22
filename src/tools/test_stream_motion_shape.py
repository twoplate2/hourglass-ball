"""Independent positive/negative controls for pixel motion and silhouette tests."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np
from PIL import Image

from check_stream_motion_shape import (
    analyze, breathing_metrics, displacement, geometry, sample_pairs, shape_metrics)


def sample_geometry():
    return geometry(dict(cx=100, outlet_y=80, surface_y=500, half_width=20,
                         radius=200, background_rgb=[232, 242, 247]))


def silhouette(taper=0, holes=False, hair=False):
    image = np.full((600, 200, 3), [232, 242, 247], dtype=np.uint8)
    for y in range(80, 500):
        half = 20 * (1 - taper * (y-80)/420)
        if hair:
            half -= (y % 3) * .8
        image[y, int(100-half):int(100+half)+1] = [190, 145, 75]
    if holes:
        image[200:280, 98:102] = [232, 242, 247]
    return image


class PixelMotionShapeTests(unittest.TestCase):
    def test_explicit_60_and_30_hz_select_actual_adjacent_frames(self):
        times=np.asarray([8+i/60 for i in range(241)])
        full=[(i,j) for i,j in sample_pairs(times,60,60) if j is not None]
        half=[(i,j) for i,j in sample_pairs(times,30,30) if j is not None]
        self.assertEqual(len(full),240)
        self.assertEqual(len(half),120)
        self.assertTrue(all(j-i==1 for i,j in full))
        self.assertTrue(all(j-i==2 for i,j in half))

    def test_legacy_defaults_preserve_original_origin_and_pair_cadence(self):
        times=np.asarray([8+i/60 for i in range(241)])
        pairs=[(i,j) for i,j in sample_pairs(times) if j is not None]
        self.assertEqual(len(pairs),40)
        self.assertEqual(pairs[0],(0,3))
        self.assertTrue(all(j-i in (3,4) for i,j in pairs))

    def test_invalid_sampling_frequency_is_rejected(self):
        for sample,pair in ((0,20),(10,-1),(float("nan"),30),(60,float("inf"))):
            with self.assertRaises(ValueError):list(sample_pairs(np.arange(10),sample,pair))

    def test_patch_measures_downward_displacement(self):
        rng = np.random.default_rng(8)
        before = rng.integers(30, 200, size=(250, 120, 3), dtype=np.uint8)
        after = np.roll(before, 7, axis=0)
        result = displacement(before, after, 60, 120, 18, 20)
        self.assertTrue(result["reliable"])
        self.assertAlmostEqual(result["dy"], 7, delta=.1)

    def test_patch_measures_upward_motion_without_calling_it_downward(self):
        rng = np.random.default_rng(10)
        before = rng.integers(30, 200, size=(250, 120, 3), dtype=np.uint8)
        after = np.roll(before, -5, axis=0)
        result = displacement(before, after, 60, 120, 18, 20)
        self.assertTrue(result["reliable"])
        self.assertAlmostEqual(result["dy"], -5, delta=.1)

    def test_static_column_shading_cannot_win_over_actual_grain_motion(self):
        rng=np.random.default_rng(333)
        bias=rng.integers(50,180,(1,120,1)).astype(float)
        noise=rng.integers(-8,9,(250,120,1)).astype(float)
        before=np.repeat(np.clip(bias+noise,0,255),3,axis=2).astype(np.uint8)
        changed=bias+np.sin(np.arange(120)[None,:,None]*.71)*80
        after=np.repeat(np.clip(changed+np.roll(noise,3,axis=0),0,255),3,axis=2).astype(np.uint8)
        after[72:109]=np.repeat(np.clip(bias+rng.integers(-8,9,(37,120,1)),0,255),3,axis=2).astype(np.uint8)
        result=displacement(before,after,60,120,18,40)
        # Strongly changed/clipped lighting may legitimately fail confidence;
        # it must never be accepted as the distractor's -30px motion.
        self.assertAlmostEqual(result["dy"],3,delta=.15)

    def test_one_way_match_with_ambiguous_reverse_is_rejected(self):
        rng=np.random.default_rng(334)
        before=rng.integers(40,210,(300,120,3),dtype=np.uint8)
        before[132:169]=before[82:119]
        after=rng.integers(40,210,before.shape,dtype=np.uint8)
        after[85:122]=before[82:119]
        result=displacement(before,after,60,100,18,60)
        self.assertFalse(result["reliable"])

    def test_moving_outline_does_not_move_stationary_core_texture(self):
        rng = np.random.default_rng(31)
        texture = rng.integers(80, 180, size=(300, 140, 3), dtype=np.uint8)
        images = []
        for offset in (0, 15):
            image = np.full_like(texture, 240)
            for y in range(300):
                half = int(20 + 4 * np.sin((y-offset) / 17))
                image[y,70-half:71+half] = texture[y,70-half:71+half]
            images.append(image)
        result = displacement(images[0],images[1],70,150,20,25)
        self.assertTrue(result["reliable"])
        self.assertAlmostEqual(result["dy"],0,delta=.1)

    def test_same_motion_direction_and_speed_at_60_30_10_hz(self):
        rng = np.random.default_rng(32)
        source = rng.integers(40,210,size=(800,140,3),dtype=np.uint8)
        for stride in (1,2,6):
            after=source.copy()
            after[:400] = np.roll(source[:400],stride,axis=0)
            after[400:] = np.roll(source[400:],2*stride,axis=0)
            upper=displacement(source,after,70,200,20,30)
            lower=displacement(source,after,70,600,20,30)
            self.assertTrue(upper["reliable"] and lower["reliable"])
            self.assertAlmostEqual(upper["dy"]/(stride/60),60,delta=1)
            self.assertAlmostEqual(lower["dy"]/(stride/60),120,delta=1)

    def test_blank_or_uncorrelated_texture_is_not_a_motion_witness(self):
        before = np.full((250,120,3), 120, dtype=np.uint8)
        self.assertFalse(displacement(before,before,60,120,18,20)["reliable"])
        rng = np.random.default_rng(9)
        a = rng.integers(0,255,before.shape,dtype=np.uint8)
        b = rng.integers(0,255,before.shape,dtype=np.uint8)
        self.assertFalse(displacement(a,b,60,120,18,20)["reliable"])

    def test_rectangle_and_single_grain_hair_do_not_pass_macro_shape(self):
        for hair in (False,True):
            result=shape_metrics(silhouette(hair=hair),sample_geometry())
            self.assertTrue(result["applicable"])
            self.assertLess(result["macro_width_variation"],.08)

    def test_gradual_taper_is_visible_after_smoothing(self):
        result=shape_metrics(silhouette(taper=.28),sample_geometry())
        self.assertGreater(result["macro_width_variation"],.08)
        self.assertGreater(result["mean_core_coverage"],.99)

    def test_long_transparent_lane_is_detected(self):
        result=shape_metrics(silhouette(taper=.28,holes=True),sample_geometry())
        self.assertGreater(result["longest_core_hole"],result["hole_budget"])

    def test_periodic_whole_stream_breathing_is_rejected(self):
        times=np.arange(0,6,.1)
        widths=np.column_stack([base*(1+.12*np.sin(2*np.pi*times)) for base in (40,36,32)])
        result=breathing_metrics(times,widths)
        self.assertTrue(result["evaluated"])
        self.assertTrue(result["periodic_breathing"])

    def test_static_taper_and_small_local_edge_noise_are_not_breathing(self):
        times=np.arange(0,6,.1)
        rng=np.random.default_rng(19)
        widths=np.array([40,36,32])[None,:]+rng.normal(0,.3,(len(times),3))
        result=breathing_metrics(times,widths)
        self.assertTrue(result["evaluated"])
        self.assertFalse(result["periodic_breathing"])

    def test_one_second_cannot_certify_absence_of_periodic_breathing(self):
        times=np.arange(0,1,.02)
        result=breathing_metrics(times,np.tile([40,36,32],(len(times),1)))
        self.assertFalse(result["evaluated"])

    def test_cancelled_recording_is_rejected_before_loading_pixels(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"report.json"
            path.write_text(json.dumps(dict(completed=False,frames=[])),encoding="utf-8")
            with self.assertRaises(ValueError):analyze(path)

    def test_real_released_35_silhouette_is_a_negative_control(self):
        archive=Path(__file__).resolve().parents[2]/"design/checks/records/v3.5/evidence.zip"
        with zipfile.ZipFile(archive) as bundle:
            report=json.loads(bundle.read("final-high/result.json"))
            frame=next(f for f in report["frames"] if f["name"]=="middle-f000")
            image=np.asarray(Image.open(io.BytesIO(bundle.read("final-high/"+frame["file"]))).convert("RGB"))
        g=frame["geometry"];height=frame["dimensions"][1]
        mapped=geometry(dict(cx=g["_cx"],outlet_y=height-frame["outlet_y"],
            surface_y=height-g["mound_top_y"],half_width=frame["half_width"],
            radius=g["_R_inner"],background_rgb=np.array(g["glass_fill_rgb"])*255))
        result=shape_metrics(image,mapped)
        self.assertTrue(result["applicable"])
        self.assertLess(result["macro_width_variation"],.08)
        self.assertGreater(result["mean_core_coverage"],.85)


if __name__ == "__main__":
    unittest.main()
