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

    def test_thin_support_preserves_grain_pixels_and_particle_centres(self):
        xs=np.asarray([-1.07,1.07]);ys=np.asarray([90.,150.]);pitch=.8
        v=particle_vertices(xs,ys,[.4,2.4],[1,2],pitch,aperture_half=2.545263157894736)
        centres=v[:,:,:2].mean(axis=1)
        np.testing.assert_allclose(centres,np.column_stack((xs,ys)),atol=2e-5,rtol=0)
        _,radius=unpack_appearance(v[:,0,4],pitch)
        local_pixels=v[:,:,2:4]*radius[:,None,None]*np.asarray([1,1.45])
        np.testing.assert_allclose(local_pixels,v[:,:,:2]-centres[:,None,:],atol=2e-5,rtol=0)
        # Extra lateral support keeps the original grain-pixel coordinates;
        # real GL separately checks coverage, aperture clipping and appearance.
        self.assertTrue((v[:,1,0]-v[:,0,0] > 4.2).all())
        self.assertTrue((v[:,1,1]-v[:,0,1] == 0).all())

    def test_wide_support_keeps_original_vertices_bit_for_bit(self):
        for half in (17.,30.):
            old=particle_vertices([20,80],[90,150],[.4,2.4],[1,2],1.85)
            new=particle_vertices([20,80],[90,150],[.4,2.4],[1,2],1.85,aperture_half=half)
            np.testing.assert_array_equal(new,old)

    def test_local_shape_coordinates_measure_actual_sprite_pixels(self):
        for pitch in (.8,1.85,4.0):
            with self.subTest(pitch=pitch):
                v=particle_vertices([20,80],[90,150],[.4,2.4],[1,2],pitch)
                _,radius=unpack_appearance(v[:,0,4],pitch)
                local_pixels=v[:,:,2:4]*radius[:,None,None]*np.asarray([1,1.45])
                actual_pixels=v[:,:,:2]-np.asarray([[20,90],[80,150]])[:,None,:]
                np.testing.assert_allclose(local_pixels,actual_pixels,atol=2e-5,rtol=0)


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


class StreamFringeTests(unittest.TestCase):
    def test_no_whole_carrier_shift_outside_dense_stream(self):
        from short_grain_stream import art_stream_positions
        # The rejected 20%-of-carriers trial grows multi-grain bumps here.
        phase = np.linspace(0, 6.28, 4001)
        for half, pitch in [(12, 2.5), (19, 1.6), (30, 2)]:
            x = np.full_like(phase, .78*half)
            drawn = art_stream_positions(x, np.full_like(phase, -10*half), phase,
                                          center=0, outlet=0, half=half, pitch=pitch)
            self.assertTrue(np.all(drawn <= x), 'Whole-carrier rim bumps returned')


class FringeSupportTests(unittest.TestCase):
    def test_unresolved_thin_stream_keeps_original_support(self):
        args=([3.],[100.],[.01],[1],.8)
        original=particle_vertices(*args,aperture_half=7.,fringe=True)
        resolved=particle_vertices(*args,aperture_half=7.,fringe=True,center=0.)
        np.testing.assert_array_equal(resolved,original)

    def test_outer_band_keeps_room_when_the_dense_core_contracts(self):
        import inspect
        self.assertIn('center',inspect.signature(particle_vertices).parameters)
        for x in (14.,11.,8.):
            v=particle_vertices([x],[200.],[.01],[2],1.85,
                                aperture_half=19.,fringe=True,center=0.)
            self.assertGreater(v[0,:,0].max(),1.12*19.+max(.8,.26*sprite_radius(1.85,2)))
            self.assertAlmostEqual(float(v[0,:,0].mean()),x,places=5)

    def test_sparse_outer_grains_keep_the_existing_particle_centres_and_identity(self):
        phases=np.linspace(0.,math.tau,2001,endpoint=False)
        x=np.full(len(phases),13.);y=np.full(len(phases),200.)
        sizes=np.ones_like(phases);pitch=1.85
        v=particle_vertices(x,y,phases,sizes,pitch,aperture_half=19,fringe=True,center=0.)
        radius=sprite_radius(pitch,sizes)
        outer=np.ptp(v[:,:,0],axis=1)>4.5*radius
        self.assertGreater(np.count_nonzero(outer),0,'No room for separated edge grains')
        self.assertLess(np.mean(outer),.09,'Do not enlarge the whole stream fringe')
        np.testing.assert_allclose(v[:,:,:2].mean(axis=1),np.column_stack((x,y)),atol=2e-5,rtol=0)
        moved=particle_vertices(x+1,y-20,phases,sizes,pitch,aperture_half=19,fringe=True,center=1.)
        np.testing.assert_allclose(moved[:,:,:2]-v[:,:,:2],
                                  np.broadcast_to([1.,-20.],v[:,:,:2].shape),atol=3e-5,rtol=0)
        np.testing.assert_array_equal(moved[:,:,2:],v[:,:,2:])

    def test_expansion_keeps_centres_and_grain_pixel_scale(self):
        xs=np.array([-13.,0.,13.]); ys=np.array([160.,200.,240.])
        phases=np.array([.1,2.,6.]); sizes=np.array([1,2,1]);pitch=1.85
        old=particle_vertices(xs,ys,phases,sizes,pitch,aperture_half=19)
        new=particle_vertices(xs,ys,phases,sizes,pitch,aperture_half=19,fringe=True)
        centres=np.column_stack((xs,ys))
        np.testing.assert_allclose(new[:,:,:2].mean(axis=1),centres,atol=2e-5,rtol=0)
        np.testing.assert_array_equal(new[:,:,4],old[:,:,4])
        np.testing.assert_array_equal(new[:,:,1],old[:,:,1])
        radii=sprite_radius(pitch,sizes)
        np.testing.assert_allclose(new[:,:,2:4]*radii[:,None,None]*[1,1.45],
                                   new[:,:,:2]-centres[:,None,:],atol=2e-5,rtol=0)
        self.assertTrue(np.all(np.ptp(new[:,:,0],axis=1)>np.ptp(old[:,:,0],axis=1)))


if __name__=='__main__':unittest.main()
