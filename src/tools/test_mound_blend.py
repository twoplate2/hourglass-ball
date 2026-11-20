"""Continuous mound geometry must retain the original weighted volume model."""
import ast
from bisect import bisect_right
from copy import deepcopy
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/app'))


def model():
    tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
    nodes=[deepcopy(n) for n in tree.body if isinstance(n,ast.ClassDef)
           and n.name in ('_MoundArea','_MoundProfile')]
    nodes += [deepcopy(n) for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_smoothstep']
    widget=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HourglassWidget')
    nodes += [deepcopy(n) for n in widget.body if isinstance(n,ast.FunctionDef) and n.name=='_sync_mound_frame']
    ns=dict(math=math,bisect_right=bisect_right,_np=np,MOUND_AREA_SAMPLES=128,UPPER_ROUGH_PERIOD=48.,
            FLOW_RENDERER="b13")
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'mound-model','exec'),ns)
    cls=ns['_MoundProfile'];xs=np.linspace(-180,180,65)
    a=cls(180,(-.6*np.abs(xs)+np.sin(xs/20)*1.7).tolist())
    b=cls(180,(-.6*np.abs(xs)+np.cos(xs/27)*1.2).tolist())
    w=SimpleNamespace(elapsed=0.,_mound_frames=[(a.shape,a),(b.shape,b)],
                      _mound_frame_k=None,_mound_shape_cache=None,_mound_curve_cache=None,
                      _mound_blend_columns=None, _taper={"t_in": 27.}, _R_inner=180., _sand_landed=.5)
    return ns,w


class MoundBlendTests(unittest.TestCase):
    def test_first_deposits_have_shallow_slopes_and_keep_their_volume(self):
        ns, w = model()
        slopes = []
        for fraction in (.0001, .01, .04, .1):
            w._sand_landed = fraction
            ns['_sync_mound_frame'](w)
            profile = w._mound_profile
            slopes.append(profile.slope_l)
            apex = profile.apex_for_fraction(fraction)
            xs = np.asarray(profile.xs)
            edges = np.r_[-profile.radius, .5 * (xs[1:] + xs[:-1]), profile.radius]
            weights = .5 * np.diff(edges * abs(edges))
            floor, roof = np.asarray([profile.bounds(x) for x in xs]).T
            fill = np.asarray([profile.contact(x, apex) for x in xs]) - floor
            self.assertAlmostEqual(np.dot(weights, fill) / np.dot(weights, roof - floor), fraction, places=9)
        self.assertTrue(np.all(np.diff(slopes) > 0.))
        self.assertLess(slopes[0], .25 * slopes[-1])

    def test_phase_boundary_has_no_shape_step(self):
        ns,w=model();sync=ns['_sync_mound_frame']
        w.elapsed=23.999;sync(w);before=np.array(w._mound_shape)
        w.elapsed=24.001;sync(w);after=np.array(w._mound_shape)
        self.assertLess(float(np.max(abs(after-before))),.001)

    def test_paused_phase_reuses_identical_geometry(self):
        ns,w=model();w.elapsed=8.25;ns['_sync_mound_frame'](w);p=w._mound_profile
        ns['_sync_mound_frame'](w);self.assertIs(w._mound_profile,p)

    def test_scalar_volume_fallback_matches_vector_path(self):
        import mound_blend as blend
        ns,w=model();w.elapsed=8.25;ns['_sync_mound_frame'](w)
        expected=[w._mound_profile.apex_for_fraction(f) for f in (.0001,.01,.5,.99)]
        saved=blend.np
        try:
            blend.np=None
            w._mound_frame_k=None;w._mound_blend_columns=None
            ns['_sync_mound_frame'](w)
            actual=[w._mound_profile.apex_for_fraction(f) for f in (.0001,.01,.5,.99)]
        finally:blend.np=saved
        np.testing.assert_allclose(actual,expected,rtol=0,atol=1e-8)

    def test_blended_contact_has_exact_weighted_volume(self):
        ns,w=model();sync=ns['_sync_mound_frame']
        for time in (0.,.001,8.,23.999,24.,47.999,48.):
            w.elapsed=time;sync(w);p=w._mound_profile;r=p.radius
            for fraction in (0.,.0001642267,.0028622372,.1,.5,.95,1.):
                apex=p.apex_for_fraction(fraction);volume=capacity=0.
                for i,x in enumerate(p.xs):
                    left=(p.xs[i-1]+x)/2 if i else -r
                    right=(x+p.xs[i+1])/2 if i+1<len(p.xs) else r
                    weight=(right*abs(right)-left*abs(left))*.5
                    floor,roof=p.bounds(x)
                    volume+=weight*(p.contact(x,apex)-floor);capacity+=weight*(roof-floor)
                self.assertAlmostEqual(volume/capacity,fraction,places=9)
                self.assertTrue(np.allclose(p.contact_np(np.array(p.xs),apex),
                                            [p.contact(x,apex) for x in p.xs],rtol=0,atol=1e-10))


if __name__=='__main__':unittest.main()
