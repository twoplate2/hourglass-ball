"""Numerical contracts for one cached dry-sand tile; no window or global RNG."""
import importlib
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch
import tempfile

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))


class GranularMaterialTests(unittest.TestCase):
    def generate(self,*args,**kwargs):
        try:module=importlib.import_module('granular_material')
        except ModuleNotFoundError:self.fail('缺少独立、可平铺的细沙材质生成器')
        return module.granular_noise(*args,**kwargs)

    def test_deterministic_float_tile_and_seed_changes(self):
        a=self.generate(128,1.8,721)
        self.assertEqual(a.shape,(128,128));self.assertEqual(a.dtype,np.float32)
        self.assertTrue(np.isfinite(a).all())
        self.assertTrue(((a>=0)&(a<=1)).all())
        np.testing.assert_array_equal(a,self.generate(128,1.8,721))
        self.assertGreater(float(np.abs(a-self.generate(128,1.8,722)).mean()),.15)

    def test_mean_and_strength_stay_constant_across_all_grain_levels(self):
        for coarse in (1.,1.4,1.8,2.2,2.6,3.,6.):
            with self.subTest(coarse=coarse):
                tile=self.generate(128,coarse,721)
                self.assertAlmostEqual(float(tile.mean()),.5,delta=.003)
                self.assertAlmostEqual(float(tile.std()),1/np.sqrt(12),delta=.004)

    def test_coarser_grains_change_scale_without_adding_strength(self):
        scores=[]
        for coarse in (1.,1.8,3.,6.):
            tile=self.generate(256,coarse,721)
            scores.append(float(np.abs(tile-np.roll(tile,1,axis=1)).mean()))
        self.assertTrue(all(a>b for a,b in zip(scores,scores[1:])),scores)
        self.assertLess(scores[-1],.55*scores[0])

    def test_tile_repeats_in_both_axes_and_translates_without_resynthesis(self):
        tile=self.generate(128,2.6,721)
        np.testing.assert_array_equal(tile,self.generate(128,2.6,721,origin=(128,-256)))
        shifted=self.generate(128,2.6,721,origin=(7,-13))
        np.testing.assert_array_equal(shifted,np.roll(tile,(13,-7),axis=(0,1)))

    def test_wrapped_border_is_not_an_exceptional_dark_seam(self):
        tile=self.generate(512,3.,721)
        for axis in (0,1):
            difference=np.abs(tile-np.roll(tile,1,axis=axis))
            seam=np.take(difference,0,axis=axis)
            self.assertLess(float(seam.mean()),1.45*float(difference.mean()))
            self.assertLess(abs(float(np.take(tile,0,axis=axis).mean())-.5),.055)

    def test_generation_does_not_consume_either_global_rng(self):
        py_state=random.getstate();np_state=np.random.get_state()
        self.generate(96,2.2,721)
        self.assertEqual(random.getstate(),py_state)
        after=np.random.get_state()
        self.assertEqual(np_state[0],after[0]);np.testing.assert_array_equal(np_state[1],after[1])
        self.assertEqual(np_state[2:],after[2:])

    def test_cached_pigment_cannot_be_corrupted_by_a_caller(self):
        tile=self.generate(128,1.8,721)
        saved=tile.copy()
        with self.assertRaises(ValueError):tile[0,0]=0
        np.testing.assert_array_equal(self.generate(128,1.8,721),saved)

    def test_invalid_dimensions_or_coarseness_are_rejected(self):
        for size,coarse in ((0,1),(-8,1),(16,0),(16,-1),(16,float('nan'))):
            with self.subTest(size=size,coarse=coarse):
                with self.assertRaises(ValueError):self.generate(size,coarse,721)


class PackedMaterialTests(unittest.TestCase):
    levels=(1.,1.4,1.8,2.2,2.6,3.)

    def module(self):
        module=importlib.import_module('granular_material')
        self.assertTrue(callable(getattr(module,'material_noise',None)),
                        '默认六档必须读取已核对的预生成材质，而不是启动时重算')
        return module

    def test_six_packaged_grayscale_tiles_match_the_original_generator(self):
        from PIL import Image
        module=self.module();root=Path(__file__).resolve().parents[2]
        module.material_noise.cache_clear()
        for index,coarse in enumerate(self.levels,1):
            with self.subTest(coarse=coarse):
                path=root/'ui/grains'/('packed-grain-'+str(index)+'.png')
                self.assertTrue(path.is_file(),'缺少公开APK必需材质资源 '+str(path))
                with Image.open(path) as image:
                    self.assertEqual((image.mode,image.size),('L',(512,512)))
                    bins=np.asarray(image,dtype=np.uint8)
                original=module.granular_noise(512,coarse,721)
                np.testing.assert_array_equal(bins,np.floor(original*256).astype(np.uint8))
                loaded=module.material_noise(512,coarse,721)
                self.assertEqual(loaded.dtype,np.float32)
                self.assertLessEqual(float(np.max(np.abs(loaded-original))),1/512)
                self.assertAlmostEqual(float(loaded.mean()),.5,delta=.001)
                self.assertAlmostEqual(float(loaded.std()),1/np.sqrt(12),delta=.001)

    def test_android_resource_patterns_cover_every_grain_tile(self):
        import configparser
        import fnmatch
        root=Path(__file__).resolve().parents[2]
        config=configparser.ConfigParser(interpolation=None)
        config.read(root/'buildozer.spec',encoding='utf8')
        patterns=[p.strip() for p in config['app']['source.include_patterns'].split(',')]
        for index in range(1,7):
            resource='ui/grains/packed-grain-%d.png'%index
            self.assertTrue(any(fnmatch.fnmatchcase(resource,p) for p in patterns),
                            'APK resource patterns do not cover '+resource)

    def test_default_loading_and_readonly_cache_do_not_invoke_generation(self):
        module=self.module();module.material_noise.cache_clear()
        with patch.object(module,'granular_noise',side_effect=AssertionError('默认材质发生运行时生成')):
            first=module.material_noise(512,1.8,721)
            self.assertIs(module.material_noise(512,1.8,721),first)
            with self.assertRaises(ValueError):first[0,0]=0

    def test_nonstandard_parameters_still_use_the_original_generator(self):
        module=self.module();module.material_noise.cache_clear()
        for args in ((64,1.8,721),(512,1.8,17),(512,2.1,721)):
            with self.subTest(args=args):
                expected=module.granular_noise(*args)
                np.testing.assert_array_equal(module.material_noise(*args),expected)

    def test_missing_resource_uses_deterministic_generation(self):
        module=self.module();module.material_noise.cache_clear()
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(module,'_PACKED_DIR',Path(temporary)):
                try:
                    np.testing.assert_array_equal(module.material_noise(512,1.8,721),
                                                  module.granular_noise(512,1.8,721))
                finally:module.material_noise.cache_clear()

    def test_resource_loading_does_not_consume_global_rng(self):
        module=self.module();module.material_noise.cache_clear()
        py_state=random.getstate();np_state=np.random.get_state()
        module.material_noise(512,2.2,721)
        self.assertEqual(random.getstate(),py_state)
        after=np.random.get_state()
        self.assertEqual(np_state[0],after[0]);np.testing.assert_array_equal(np_state[1],after[1])
        self.assertEqual(np_state[2:],after[2:])


class StableGrainOrderTests(unittest.TestCase):
    def order(self,phases,trails):
        module=importlib.import_module('granular_material')
        helper=getattr(module,'stable_grain_order',None)
        self.assertTrue(callable(helper),'缺少由不可变粒子身份决定的全局绘制排序')
        return helper(phases,trails)

    @staticmethod
    def identities(count):
        rng=np.random.default_rng(315)
        return rng.random(count)*np.pi*2,.018+rng.random(count)*.014

    def test_permutation_is_deterministic_and_does_not_change_input(self):
        phases,trails=self.identities(173);saved=(phases.copy(),trails.copy())
        order=self.order(phases,trails)
        self.assertEqual(order.dtype,np.dtype(np.intp))
        np.testing.assert_array_equal(np.sort(order),np.arange(len(phases)))
        np.testing.assert_array_equal(order,self.order(phases,trails))
        self.assertFalse(np.array_equal(order,np.arange(len(phases))))
        self.assertFalse(np.array_equal(order,np.arange(len(phases))[::-1]))
        np.testing.assert_array_equal(phases,saved[0]);np.testing.assert_array_equal(trails,saved[1])

    def test_input_reordering_keeps_identity_depth_order(self):
        phases,trails=self.identities(173)
        canonical=self.order(phases,trails)
        shuffled=np.random.default_rng(914).permutation(len(phases))
        np.testing.assert_array_equal(shuffled[self.order(phases[shuffled],trails[shuffled])],canonical)

    def test_colliding_folded_identities_do_not_flip_after_reordering(self):
        # Distinct finite identities with equal XOR-folded 32-bit words.
        # A depth-only stable argsort would reverse them when storage reverses.
        phases=np.asarray([0x3FF0000000000000,0x3FF0000100000001],dtype=np.uint64).view(np.float64)
        trails=np.asarray([.025,.025]);canonical=self.order(phases,trails)
        shuffled=np.asarray([1,0])
        np.testing.assert_array_equal(shuffled[self.order(phases[shuffled],trails[shuffled])],canonical)

    def test_compaction_preserves_survivor_depth_order(self):
        phases,trails=self.identities(257);canonical=self.order(phases,trails)
        keep=np.arange(257)%3!=1;survivors=np.flatnonzero(keep)
        actual=survivors[self.order(phases[keep],trails[keep])]
        np.testing.assert_array_equal(actual,canonical[keep[canonical]])

    def test_growth_and_4096_chunk_boundaries_do_not_reorder_existing_ids(self):
        phases,trails=self.identities(8193);expanded=self.order(phases,trails)
        for count in (4095,4096,4097):
            np.testing.assert_array_equal(expanded[expanded<count],self.order(phases[:count],trails[:count]))
        shuffled=np.roll(np.arange(8193),4096)
        actual=shuffled[self.order(phases[shuffled],trails[shuffled])]
        np.testing.assert_array_equal(actual,expanded)

    def test_lists_empty_pool_and_duplicate_identities(self):
        empty=self.order([],[])
        self.assertEqual(empty.shape,(0,));self.assertEqual(empty.dtype,np.dtype(np.intp))
        phases,trails=self.identities(17)
        np.testing.assert_array_equal(self.order(phases.tolist(),trails.tolist()),self.order(phases,trails))
        np.testing.assert_array_equal(self.order([.25]*5,[.025]*5),np.arange(5))

    def test_no_global_rng_is_consumed(self):
        phases,trails=self.identities(173)
        py_state=random.getstate();np_state=np.random.get_state()
        self.order(phases,trails)
        self.assertEqual(random.getstate(),py_state)
        after=np.random.get_state()
        self.assertEqual(np_state[0],after[0]);np.testing.assert_array_equal(np_state[1],after[1])
        self.assertEqual(np_state[2:],after[2:])

    def test_invalid_identity_arrays_are_rejected(self):
        for phases,trails in (([1.],[]),([[1.]],[[.02]]),([float('nan')],[.02]),([1.],[float('inf')])):
            with self.subTest(phases=phases,trails=trails):
                with self.assertRaises(ValueError):self.order(phases,trails)


if __name__=='__main__':unittest.main()
