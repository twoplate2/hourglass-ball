"""Airborne splash brightness must survive while landed grains merge into the pile."""
import ast
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/app'))


def curve():return [0,10,0,0,20,10,1,0,20,100,1,1,0,100,0,1]


def colours(ys,rests=None,*,xs=None,vys=None,grain=.35,stills=None,seeds=None):
    from short_grain_stream import splash_colours
    n=len(ys)
    return splash_colours(n,xs if xs is not None else [5]*n,ys,
        vys if vys is not None else [-20]*n,rests if rests is not None else [0]*n,
        stills if stills is not None else [0]*n,seeds if seeds is not None else [.81]*n,
        [.5]*n,curve(),0,(.8,.6,.3),(.95,.8,.5),grain)


class FakeColor:
    def __init__(self,*args,rgba=None):self.rgba=rgba or args


class FakeRect:
    def __init__(self,*,pos,size):self.pos=pos;self.size=size


class FakeGroup:
    def __init__(self):self.children=[]
    def add(self,node):self.children.append(node)


def draw_method():
    tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HourglassWidget')
    fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_draw_splashes')
    ns=dict(Color=FakeColor,Rectangle=FakeRect,SAND_MATERIAL_GRAIN=.35)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'main.py:draw_splashes','exec'),ns)
    return ns['_draw_splashes']


def widget(batch):
    submitted=[]
    def old(n,x,y,hw,hh):submitted.append(None)
    def tinted(n,x,y,hw,hh,rgba):submitted.append(np.asarray(rgba).copy())
    w=SimpleNamespace(_sn=2,sx=np.array([5.,8.]),sy=np.array([30.,10.]),
        svy=np.array([-20.,0.]),srest=np.array([0.,.15]),sstill=np.zeros(2),sgd=np.array([.81,.92]),
        shh=np.array([.5,.5]),srw=np.array([.5,.5]),srh=np.array([.5,.5]),
        _mound_carve=SimpleNamespace(_v=curve()),_lower_sand_bot=0.,
        sand_base=(.8,.6,.3),sand_light=(.95,.8,.5),_splash_color=SimpleNamespace(rgba=(1,1,1,1)),
        _splash_group=FakeGroup(),_splash_rects=[],
        _splash_batch=SimpleNamespace(update_arrays=old,update_tinted_arrays=tinted) if batch else None)
    return w,submitted


class SplashColourTests(unittest.TestCase):
    def test_airborne_grains_keep_the_original_bright_palette(self):
        np.testing.assert_array_equal(colours([30,50]),[[242,204,128,255]]*2)

    def test_landed_grains_match_the_pile_instead_of_forming_a_light_rim(self):
        rgb=np.asarray(colours([10]*4,[.01,.1,.3,.5],seeds=[.65,.81,1.1,1.5]))[:,:3]
        base=np.array([204,153,76.5])
        self.assertLessEqual(np.abs(rgb-base).max(),7)
        self.assertGreater(np.ptp(rgb[:,0]),0)

    def test_descending_grain_changes_colour_continuously_near_contact(self):
        ys=np.linspace(10,13,301)
        rgb=np.asarray(colours(ys))[:,:3].astype(int)
        self.assertLessEqual(np.abs(np.diff(rgb,axis=0)).max(),1)
        self.assertGreater(rgb[-1,0],rgb[0,0]+20)

    def test_splash_rising_above_the_pile_keeps_its_bright_style(self):
        np.testing.assert_array_equal(colours([10.6],vys=[20]),[[242,204,128,255]])

    def test_surface_colour_follows_immutable_grain_seed_not_array_slot_or_clock(self):
        a=np.asarray(colours([10,10],[.1,.2],xs=[4,8],seeds=[.81,1.1]))
        b=np.asarray(colours([10,10],[.5,.4],xs=[9,5],seeds=[1.1,.81]))
        np.testing.assert_array_equal(a,b[::-1])

    def test_zero_material_strength_keeps_landed_grains_flat(self):
        np.testing.assert_array_equal(colours([10,10],[.1,.4],grain=0,seeds=[.81,1.1]),[[204,153,76,255]]*2)

    def test_no_numpy_uses_the_same_quantised_colours(self):
        expected=np.asarray(colours([30,11,10],[0,0,.2],seeds=[.81,.94,1.1]))
        with patch.dict(sys.modules,{'numpy':None}):
            result=colours([30,11,10],[0,0,.2],seeds=[.81,.94,1.1])
        np.testing.assert_array_equal(result,expected)

    def test_actual_batch_draw_receives_surface_and_air_colours_without_changing_physics(self):
        w,submitted=widget(True)
        before={key:getattr(w,key).copy() for key in ('sx','sy','svy','srest','sstill','sgd','srw','srh')}
        draw_method()(w)
        self.assertIsNotNone(submitted[-1], 'Old uniform light drawing cannot merge surface grains')
        self.assertFalse(np.array_equal(submitted[-1][0],submitted[-1][1]))
        for key,value in before.items():np.testing.assert_array_equal(getattr(w,key),value)

    def test_rect_and_batch_paths_read_identical_colours_and_clear_empty_frames(self):
        fn=draw_method();a,submitted=widget(True);b,_=widget(False)
        fn(a);fn(b)
        actual=np.array([np.rint(np.array(node.rgba)*255).astype(int) for node in b._splash_group.children if isinstance(node,FakeColor)])
        np.testing.assert_array_equal(actual,submitted[-1])
        b._sn=0;fn(b)
        for node in b._splash_group.children:
            if isinstance(node,FakeRect):self.assertEqual(node.size,(0,0))

    def test_rebuilt_canvas_receives_new_rectangles(self):
        fn=draw_method();w,_=widget(False);fn(w)
        w._splash_group=FakeGroup();w._splash_rects=[];fn(w)
        self.assertEqual(sum(isinstance(n,FakeRect) for n in w._splash_group.children),2)


if __name__=='__main__':unittest.main()
