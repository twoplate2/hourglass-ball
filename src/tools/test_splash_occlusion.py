"""Actual drawing paths must not paint buried splash grains through the pile."""
import ast
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import numpy as np
from test_splash_colours import FakeColor, FakeRect, FakeGroup

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/app'))


def draw_method():
    tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HourglassWidget')
    method=next((n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_draw_splashes'),None)
    if method is None:
        redraw=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='redraw')
        i=next(i for i,n in enumerate(redraw.body) if isinstance(n,ast.Assign)
               and any(isinstance(t,ast.Name) and t.id=='_batch' for t in n.targets))
        method=ast.parse('def _draw_splashes(self): pass').body[0]
        method.body=redraw.body[i:i+2]
    module=ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[]))
    ns=dict(Color=FakeColor,Rectangle=FakeRect,SAND_MATERIAL_GRAIN=.35)
    exec(compile(module,'main.py:splash_drawing','exec'),ns)
    return ns['_draw_splashes']


def carve(x0,y0,x1,y1):
    return [x0,y0,0,0,x1,y1,1,0,x1,100,1,1,x0,100,0,1]


def render(batch,xs,ys,hws,hhs,curve):
    captured=[]
    def submit(n,x,y,hw,hh):captured.append((n,np.array(x),np.array(y),np.array(hw),np.array(hh)))
    def tinted(n,x,y,hw,hh,rgba):submit(n,x,y,hw,hh)
    w=SimpleNamespace(_sn=len(xs),sx=np.array(xs,float),sy=np.array(ys,float),
        srw=np.array(hws,float),srh=np.array(hhs,float),_mound_carve=SimpleNamespace(_v=curve),
        _lower_sand_bot=0,_splash_group=FakeGroup(),_splash_rects=[],
        svy=np.full(len(xs),-20.),srest=np.zeros(len(xs)),sstill=np.zeros(len(xs)),
        sgd=np.full(len(xs),.81),shh=np.array(hhs,float),
        sand_base=(.8,.6,.3),sand_light=(.95,.8,.5),_splash_color=SimpleNamespace(rgba=(1,1,1,1)),
        _splash_batch=SimpleNamespace(update_arrays=submit,update_tinted_arrays=tinted) if batch else None)
    w._sync_rects_arrays=lambda group,rects,*args:submit(*args)
    original=[v.copy() for v in (w.sx,w.sy,w.srw,w.srh)]
    draw_method()(w)
    if not batch:
        submit(w._sn,w.sx,w.sy,[r.size[0]/2 for r in w._splash_rects[:w._sn]],
               [r.size[1]/2 for r in w._splash_rects[:w._sn]])
    for a,b in zip(original,(w.sx,w.sy,w.srw,w.srh)):np.testing.assert_array_equal(a,b)
    return captured[-1]


class SplashOcclusionTests(unittest.TestCase):
    def test_buried_grains_have_no_visible_quad_in_either_path(self):
        for batch in (False,True):
            n,x,y,hw,hh=render(batch,[5,10,15],[1,10,15],[1]*3,[1]*3,carve(0,10,20,10))
            self.assertEqual(n,3)
            np.testing.assert_array_equal(hw,[0,1,1])
            np.testing.assert_array_equal(hh,[0,1,1])

    def test_visible_downhill_corner_is_retained(self):
        for batch in (False,True):
            result=render(batch,[0],[100],[1.5],[.5],carve(-2,103,2,99))
            self.assertEqual(result[3][0],1.5)

    def test_empty_frame_still_clears_both_paths(self):
        for batch in (False,True):
            result=render(batch,[],[],[],[],carve(0,10,20,10))
            self.assertEqual(result[0],0)

    def test_scalar_without_numpy_has_the_same_occlusion(self):
        from short_grain_stream import visible_splash_sizes
        with patch.dict(sys.modules,{'numpy':None}):
            hw,hh=visible_splash_sizes(3,[5,10,15],[1,10,15],[1]*3,[1]*3,carve(0,10,20,10),0)
        self.assertEqual(hw,[0,1,1]);self.assertEqual(hh,[0,1,1])


if __name__=='__main__':unittest.main()
