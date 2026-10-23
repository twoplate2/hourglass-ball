"""Material sampling must measure actual sprite pixels, including large grains."""
from pathlib import Path
import math
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from short_grain_stream import particle_vertices,sprite_radius
import short_grain_stream as renderer


def unpack_appearance(codes,pitch):
    large=(codes>=.75).astype(float)
    return 2*(codes-large),sprite_radius(pitch,1+large)


class ParticleMaterialTests(unittest.TestCase):
    def test_fine_cluster_keeps_reference_support_area(self):
        from short_grain_stream import sprite_aspect
        reference=1.8517562
        def area(pitch):
            v=particle_vertices([0],[0],[.7],[2],pitch,sprite_aspect(pitch,reference))[0,:,:2]
            a,b=v[1]-v[0],v[3]-v[0]
            return abs(a[0]*b[1]-a[1]*b[0])
        self.assertAlmostEqual(area(1.4619128)/area(reference),1,places=6)

    def test_compensation_is_bounded_and_leaves_coarse_fill_alone(self):
        from short_grain_stream import sprite_aspect
        self.assertEqual(sprite_aspect(2.5,1.85),1.45)
        self.assertEqual(sprite_aspect(.1,.2),1.45)
        self.assertLessEqual(sprite_aspect(.1,100),2.4)

    def test_source_seed_and_size_survive_the_vertex_payload(self):
        phases=np.asarray([0,.01,1.2,math.pi,6.2,math.tau]*2)
        sizes=np.asarray([1]*6+[2]*6)
        v=particle_vertices(np.arange(12),np.arange(12)*2,phases,sizes,1.85)
        seed,radius=unpack_appearance(v[:,0,4],1.85)
        np.testing.assert_allclose(seed,(phases/math.tau)%1,atol=2e-7,rtol=0)
        np.testing.assert_allclose(radius,sprite_radius(1.85,sizes),atol=1e-7,rtol=0)

    def test_texture_pixel_offsets_match_actual_geometry_at_all_sizes(self):
        for pitch in (.8,1.85,4.0):
            with self.subTest(pitch=pitch):
                v=particle_vertices([20,80],[90,150],[.4,2.4],[1,2],pitch)
                _,radius=unpack_appearance(v[:,0,4],pitch)
                material_pixels=v[:,:,2:4]*radius[:,None,None]*np.asarray([1,1.45])
                actual_pixels=v[:,:,:2]-np.asarray([[20,90],[80,150]])[:,None,:]
                np.testing.assert_allclose(material_pixels,actual_pixels,atol=2e-5,rtol=0)


class BirthMaterialPayloadTests(unittest.TestCase):
    def payload(self, times, scales, sources, jump_y=1.0):
        self.assertTrue(hasattr(renderer,'birth_material_payload'),
                        '出口材质必须冻结实际出生参数，不能继续采当前世界纹理')
        return renderer.birth_material_payload(times,scales,sources,jump_y)

    def test_birth_clock_scroll_and_startup_blend_have_distinct_time_bases(self):
        times=np.asarray([0.,.075,.15,.225,.3,8.,16.,16.125])
        scales=np.asarray([1.,2.,7.,.5,3.,2.,1.,4.])
        sources=np.arange(len(times),dtype=float)*2.5-8.
        result=self.payload(times,scales,sources)
        expected=np.column_stack((sources,(times*scales)%16.,
            (times*.125)%1.,(times*.0625)%1.,
            [0.,.15625,.5,.84375,1.,1.,1.,1.]))
        self.assertEqual(result.shape,(len(times),5))
        self.assertTrue(np.isfinite(result).all())
        np.testing.assert_allclose(result,expected,rtol=0,atol=2e-6)

    def test_phase_wraps_keep_large_birth_times_out_of_gpu_payload(self):
        times=np.asarray([7.999,8.,8.001,15.999,16.,16.001,1_000_000.125])
        scales=np.asarray([1.,1.,1.,2.,2.,2.,7.125])
        result=self.payload(times,scales,np.zeros(len(times)),jump_y=1.6)
        np.testing.assert_allclose(result[:,1],(times*scales)%16.,rtol=0,atol=2e-6)
        np.testing.assert_allclose(result[:,2],(times*.125)%1.,rtol=0,atol=2e-6)
        np.testing.assert_allclose(result[:,3],(times*.1)%1.,rtol=0,atol=2e-6)
        self.assertTrue(((result[:,1]>=0)&(result[:,1]<16)).all())
        self.assertTrue(((result[:,2:4]>=0)&(result[:,2:4]<1)).all())

    def test_frozen_birth_payload_survives_motion_pause_and_compaction(self):
        times=np.asarray([.05,1.25,16.125]);scales=np.asarray([1.,2.,3.])
        sources=np.asarray([-7.,2.,9.])
        first=self.payload(times,scales,sources)
        # Current elapsed/position intentionally have no role in this API.
        # A renderer still passing current clock is caught separately by the
        # actual upload contract in test_particle_continuity.
        for _current_elapsed in (16.2,17.9,50.):
            np.testing.assert_array_equal(self.payload(times,scales,sources),first)
        keep=np.asarray([2,0])
        np.testing.assert_array_equal(self.payload(times[keep],scales[keep],sources[keep]),first[keep])
        np.testing.assert_array_equal(times,[.05,1.25,16.125])

    def test_empty_births_have_no_phantom_material(self):
        result=self.payload(np.asarray([]),np.asarray([]),np.asarray([]))
        self.assertEqual(result.shape,(0,5))


if __name__=='__main__':unittest.main()
