"""Actual coloured batching code with in-memory graphics objects; no GL window."""
import ast
from array import array
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import numpy as np

ROOT=Path(__file__).resolve().parents[2]


class Group:
    def __init__(self):self.children=[]
    def add(self,node):self.children.append(node)
    def remove(self,node):self.children.remove(node)
    def insert(self,index,node):self.children.insert(index,node)


class Mesh:
    def __init__(self,**kwargs):self.indices=[];self._vertices=None;self.vertex_writes=0
    @property
    def vertices(self):return self._vertices
    @vertices.setter
    def vertices(self,value):self._vertices=value;self.vertex_writes+=1


class Texture:
    def __init__(self,size):self.size=size;self.observers=[];self.uploads=[];self.raw=b''
    @classmethod
    def create(cls,*,size,**kwargs):return cls(size)
    def add_reload_observer(self,fn):self.observers.append(fn)
    def blit_buffer(self,raw,**kwargs):
        self.raw=bytes(raw);self.uploads.append((self.raw,kwargs))


class Binding:
    def __init__(self,*,index):self.index=index;self.texture=None


class Context(dict,Group):
    def __init__(self,*,success=True,**kwargs):
        dict.__init__(self);Group.__init__(self)
        self.shader=types.SimpleNamespace(success=success,vs='',fs='')


def load_renderer(numpy=True):
    ns=dict(array=array,_np=np if numpy else None,BindTexture=Binding,Mesh=Mesh,Texture=Texture,
        InstructionGroup=Group,RenderContext=Context,Color=lambda *args:types.SimpleNamespace(rgba=args),
        glGetIntegerv=lambda flag:[2])
    tree=ast.parse((ROOT/'src/app/flow_batch_experiment.py').read_text(encoding='utf-8'))
    build=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build_vertices')
    exec(compile(ast.Module(body=[build],type_ignores=[]),'actual-build-vertices','exec'),ns)
    ns['flow_batch_experiment']=types.SimpleNamespace(build_vertices=ns['build_vertices'],
        blit_texture=lambda target,data,count:target.blit_buffer(data[:count*4],size=(count,1)))
    names={'CHUNK','TEXELS_PER_SPLASH','_FLOAT4','_FLT','TEXEL_STEP','TEXEL_STEP_UNIFORM',
           'PAD','VERTEX_SHADER','TINT_VERTEX_SHADER','FRAGMENT_SHADER','_SELECTORS','_INDICES'}
    tree=ast.parse((ROOT/'src/app/flow_splash_experiment.py').read_text(encoding='utf-8'))
    nodes=[n for n in tree.body if
        (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in n.targets))
        or (isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in
            {'_tint_bytes','_tint_context','SplashBatch','ColoredSplashBatch','install'})]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual-splash-batch','exec'),ns)
    return ns


def update(batch,count,rgba):
    pos=np.arange(count,dtype=float)
    batch.update_tinted_arrays(count,pos,pos+10,np.ones(count),np.ones(count)*.5,rgba)


def fake_gl_import():
    kivy=types.ModuleType('kivy');graphics=types.ModuleType('kivy.graphics')
    gl=types.ModuleType('kivy.graphics.opengl');gl.GL_MAX_VERTEX_TEXTURE_IMAGE_UNITS=1234
    kivy.graphics=graphics;graphics.opengl=gl
    return patch.dict('sys.modules',{'kivy':kivy,'kivy.graphics':graphics,'kivy.graphics.opengl':gl})


def widget_class():
    class Widget:
        def _build_dynamic_canvas(self):
            self.canvas=Group();self._splash_group=Group()
            self._splash_color=object();self._splash_group.add(self._splash_color)
            self.canvas.add(self._splash_group);self.sand_light=(.8,.6,.3)
            self.original_group=self._splash_group;self.original_color=self._splash_color
            self._splash_rects=[]
    return Widget


class SplashTintBatchTests(unittest.TestCase):
    def test_colour_bytes_match_all_slots_across_the_chunk_boundary(self):
        ns=load_renderer();batch=ns['ColoredSplashBatch'](Group())
        rgba=(np.arange(513*4)%256).astype(np.uint8).reshape(-1,4);before=rgba.copy()
        update(batch,513,rgba)
        self.assertEqual(len(batch.parts),2)
        self.assertEqual(batch.tint_parts[0][0].uploads[-1][0],rgba[:512].tobytes())
        self.assertEqual(batch.tint_parts[1][0].uploads[-1][0],rgba[512:].tobytes())
        np.testing.assert_array_equal(rgba,before)

    def test_without_numpy_scalar_colours_use_the_same_rgba_bytes(self):
        ns=load_renderer(False);batch=ns['ColoredSplashBatch'](Group())
        rgba=[[211,161,85,255],[230,184,112,255]]
        batch.update_tinted_arrays(2,[1.,2.],[3.,4.],[.5,.5],[.5,.5],rgba)
        self.assertEqual(batch.tint_parts[0][0].uploads[-1][0],bytes(sum(rgba,[])))

    def test_shrink_and_empty_frames_keep_old_geometry_hidden(self):
        ns=load_renderer();batch=ns['ColoredSplashBatch'](Group())
        update(batch,513,np.full((513,4),255,dtype=np.uint8))
        update(batch,1,np.asarray([[1,2,3,255]],dtype=np.uint8))
        self.assertEqual(batch.parts[0][4][16:32],ns['PAD'])
        self.assertEqual(len(batch.parts[1][0].indices),0)
        self.assertEqual(batch.parts[1][5],0)
        uploads=[len(p[0].uploads) for p in batch.tint_parts]
        update(batch,0,np.empty((0,4),dtype=np.uint8))
        self.assertTrue(all(not len(p[0].indices) and p[5]==0 for p in batch.parts))
        self.assertEqual(uploads,[len(p[0].uploads) for p in batch.tint_parts])

    def test_tints_do_not_rebuild_static_mesh_vertices(self):
        ns=load_renderer();batch=ns['ColoredSplashBatch'](Group())
        update(batch,2,np.full((2,4),255,dtype=np.uint8))
        mesh=batch.parts[0][0];vertices=mesh.vertices
        update(batch,2,np.asarray([[1,2,3,255],[4,5,6,255]],dtype=np.uint8))
        self.assertIs(mesh.vertices,vertices)
        self.assertEqual(mesh.vertex_writes,1)

    def test_actual_coloured_mesh_triangles_cover_the_rectangle_once(self):
        ns=load_renderer();batch=ns['ColoredSplashBatch'](Group())
        update(batch,1,np.full((1,4),255,dtype=np.uint8))
        mesh=batch.parts[0][0]
        selectors=np.asarray(mesh.vertices).reshape(-1,4)[:4,3]
        corners=np.column_stack((selectors%2,np.floor(selectors*.5)))
        triangles=corners[np.asarray(mesh.indices).reshape(-1,3)[:2]]
        for x in (.1,.3,.6,.9):
            for y in (.15,.35,.65,.85):
                if abs(x-y)<1e-8 or abs(x+y-1)<1e-8:continue
                covered=0
                for t in triangles:
                    cross=[(t[(i+1)%3,0]-t[i,0])*(y-t[i,1])
                           -(t[(i+1)%3,1]-t[i,1])*(x-t[i,0]) for i in range(3)]
                    covered+=int(all(v>=0 for v in cross) or all(v<=0 for v in cross))
                self.assertEqual(covered,1,(x,y,triangles.tolist()))

    def test_reload_replays_latest_tint_bytes_and_binding_precedes_mesh(self):
        ns=load_renderer();group=Group();batch=ns['ColoredSplashBatch'](group)
        rgba=np.asarray([[1,2,3,255],[4,5,6,255]],dtype=np.uint8);update(batch,2,rgba)
        texture,data,binding=batch.tint_parts[0]
        self.assertEqual(binding.index,2);self.assertIs(binding.texture,texture)
        self.assertLess(group.children.index(binding),group.children.index(batch.parts[0][0]))
        texture.raw=b''
        texture.observers[0](texture)
        self.assertEqual(texture.raw[:8],rgba.tobytes())
        self.assertEqual(len(texture.raw),ns['CHUNK']*4)

    def test_bad_colour_payload_fails_before_mutating_position_batches(self):
        ns=load_renderer();batch=ns['ColoredSplashBatch'](Group())
        for rgba in ([[1,2,3]],[[1,2,3,256]],np.zeros((1,4),dtype=float)):
            with self.assertRaises((ValueError,TypeError)):update(batch,1,rgba)
        self.assertEqual(batch.parts,[])

    def test_shared_uncoloured_batch_still_has_only_original_bounds_binding(self):
        ns=load_renderer();group=Group();batch=ns['SplashBatch'](group)
        batch.update_arrays(2,[1.,2.],[3.,4.],[.5,.5],[.5,.5])
        self.assertEqual([n.index for n in group.children if isinstance(n,Binding)],[1])
        self.assertEqual(batch.parts[0][3].size,(2048,1))

    def test_insufficient_samplers_leave_widget_builder_unmodified(self):
        ns=load_renderer();widget=widget_class();original=widget._build_dynamic_canvas
        ns['glGetIntegerv']=lambda flag:[1]
        with fake_gl_import(),self.assertRaisesRegex(RuntimeError,'two vertex'):
            ns['install'](widget)
        self.assertIs(widget._build_dynamic_canvas,original)
        self.assertFalse(hasattr(widget,'_sync_splashes'))

    def test_shader_preflight_failure_leaves_widget_builder_unmodified(self):
        ns=load_renderer();widget=widget_class();original=widget._build_dynamic_canvas
        ns['RenderContext']=lambda **kwargs:Context(success=False)
        with fake_gl_import(),self.assertRaisesRegex(RuntimeError,'shader'):
            ns['install'](widget)
        self.assertIs(widget._build_dynamic_canvas,original)

    def test_build_time_texture_failure_keeps_ordinary_group_and_colour(self):
        ns=load_renderer();widget=widget_class()
        with fake_gl_import():ns['install'](widget)
        class BrokenTexture(Texture):
            @classmethod
            def create(cls,*,size,**kwargs):
                if size==(512,1):raise RuntimeError('synthetic tint allocation failure')
                return cls(size)
        ns['Texture']=BrokenTexture
        w=widget();w._build_dynamic_canvas()
        self.assertIsNone(w._splash_batch)
        self.assertIsNone(w._splash_context)
        self.assertIs(w._splash_group,w.original_group)
        self.assertIs(w._splash_color,w.original_color)
        self.assertEqual(w.canvas.children,[w.original_group])
        self.assertEqual(w.original_group.children,[w.original_color])

    def test_build_time_shader_failure_also_keeps_the_ordinary_path(self):
        ns=load_renderer();widget=widget_class()
        with fake_gl_import():ns['install'](widget)
        ns['RenderContext']=lambda **kwargs:Context(success=False)
        w=widget();w._build_dynamic_canvas()
        self.assertIsNone(w._splash_batch)
        self.assertIs(w._splash_group,w.original_group)
        self.assertIs(w._splash_color,w.original_color)
        self.assertEqual(w.canvas.children,[w.original_group])

    def test_successful_install_binds_tints_and_only_replaces_splash_group(self):
        ns=load_renderer();widget=widget_class()
        with fake_gl_import():ns['install'](widget)
        w=widget();w._build_dynamic_canvas()
        self.assertIsInstance(w._splash_batch,ns['ColoredSplashBatch'])
        self.assertEqual(w._splash_context['bounds'],1)
        self.assertEqual(w._splash_context['tints'],2)
        self.assertEqual(w._splash_context['tint_step'],1/512)
        self.assertNotIn(w.original_group,w.canvas.children)
        self.assertEqual(w._splash_context.children,[w._splash_group])


if __name__=='__main__':unittest.main()
