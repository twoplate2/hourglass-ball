"""真实粒子的出生、飞行和回收检查；不把纹理相位当作粒子身份。

运行单元用例不会启动窗口。隐藏 PC 运行器可调用 snapshot/ContinuityTrace，
在实际 update_particles 前后核对主粒子；不据此声称逐像素或性能验收。
"""
import math
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import short_grain_stream as renderer


def assert_particle_meshes(widget):
    """核对实际上传的顶点和索引，不只相信 renderer 自报的粒子数。"""
    counts = widget._particle_counts
    meshes = widget._particle_meshes
    if len(counts) != len(meshes) or sum(counts) != widget.pn:
        raise AssertionError("实际粒子与 Mesh 活跃粒子数不一致")
    centres = []
    for mesh, count in zip(meshes, counts):
        vertices = np.asarray(mesh.vertices, dtype=np.float32)
        if vertices.size % 24 or not 0 <= count <= vertices.size // 24:
            raise AssertionError("每个粒子必须保留一个完整四边形")
        active = vertices.reshape(-1, 4, 6)[:count]
        if not np.isfinite(active).all():
            raise AssertionError("在途粒子的顶点存在非有限值")
        indices = np.asarray(mesh.indices, dtype=np.int64)[:count * 6]
        if indices.size != count * 6:
            raise AssertionError("在途粒子缺少绘制三角形")
        if count:
            normalized = indices.reshape(count, 6) - np.arange(count)[:, None] * 4
            if not np.all((normalized >= 0) & (normalized <= 3)):
                raise AssertionError("绘制索引指向错误粒子")
            for triangle in (normalized[:, :3], normalized[:, 3:]):
                if np.any(np.diff(np.sort(triangle, axis=1), axis=1) == 0):
                    raise AssertionError("粒子三角形退化，实际不可见")
            if any(not np.all(np.any(normalized == corner, axis=1)) for corner in range(4)):
                raise AssertionError("粒子四边形没有完整绘制")
            centres.append(active[:, :, :2].mean(axis=1))
    actual = np.concatenate(centres) if centres else np.empty((0, 2))
    expected = np.column_stack((widget.px[:widget.pn], widget.py[:widget.pn]))
    if actual.shape != expected.shape or not np.allclose(actual, expected, rtol=0, atol=2e-4):
        raise AssertionError("实际 Mesh 没有逐颗跟随真实粒子坐标")
    return len(actual)


def snapshot(widget):
    """pwp/ptl 出生后不变；落地压紧数组时，下标不再是同一粒子。"""
    rows = {}
    appearance={}
    for i in range(widget.pn):
        key = (float(widget.pwp[i]), float(widget.ptl[i]))
        if key in rows:
            raise AssertionError("同帧粒子身份重复，无法据此追踪")
        rows[key] = (float(widget.px[i]), float(widget.py[i]),
                     float(widget.pvy[i]))
        appearance[key]=(float(widget.pxo[i]),float(widget.pwa[i]),int(widget.psz[i]))
    cx, cy, x0, scale, n1 = widget._mound_contact_curve()
    return {
        "particles": rows,
        "curve": (list(cx), list(cy)),
        "top": float(widget.get_mound_top_y()),
        "outlet": float(2 * widget._neck_y - widget._taper["y_bot"]),
        "scale": float(widget._particle_motion_scale),
        "tube_height": float(widget._tube_h),
        "aperture_half":float(widget._taper['t_in']),
        "appearance":appearance,
        "motion":dict(cx=widget._cx,cut=2*widget._neck_y-widget._taper['y_bot'],
            top=widget._lower_sand_top,center=widget._lower_y_c,radius=widget._R_inner,
            half=widget._taper['t_in'],pitch=renderer.stream_pitch(widget._R_inner,
                sys.modules[widget.__class__.__module__].SAND_MATERIAL_COARSE,widget._taper['t_in'])),
    }


def _surface(state, x):
    xs, ys = state["curve"]
    if len(xs) < 2:
        return state["top"]
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    # 查表的横坐标等距，与主粒子的接触查表一致。
    z = (x - xs[0]) * (len(xs) - 1) / (xs[-1] - xs[0])
    i = min(int(z), len(xs) - 2)
    return ys[i] + (ys[i + 1] - ys[i]) * (z - i)


class ContinuityTrace:
    """只接受积分可达堆面的回收，不允许半空消失后重用旧身份。"""
    def __init__(self):
        self.retired = set()
        self.births = self.contacts = self.survivor_steps = 0

    def observe(self, before, after, dt, spread_ratio=0.0, kill_inset=0.0):
        previous, current = before["particles"], after["particles"]
        if dt < 0 or not math.isfinite(dt):
            raise AssertionError("无效步长")
        if dt == 0 and previous != current:
            raise AssertionError("暂停时粒子的身份或位置不连续")
        gravity = -450.0 * before["scale"] ** 2
        tolerance = 2e-5
        for key, (x, y, vy) in previous.items():
            predicted = y + vy * dt + 0.5 * gravity * dt * dt
            if key in current:
                if abs(current[key][0]-x)>1.0+dt*(abs(vy)+abs(gravity*dt)):
                    raise AssertionError("同一粒子的横向位置突变")
                if 'motion' in before:
                    m=before['motion'];off,amp,size=before['appearance'][key]
                    if after['appearance'][key]!=before['appearance'][key]:
                        raise AssertionError('同一粒子的粒径或运动身份突变')
                    depth=max(0,m['cut']-predicted)
                    speed=60*before['scale']
                    target=max(.87,math.sqrt(speed/math.sqrt(speed*speed+2*abs(gravity)*depth)))
                    shrink=1 if predicted>m['cut'] else 1+(target-1)*min(1,depth/92)
                    fallen=max(0,m['cut']-predicted)
                    raw=off*shrink+math.sin(fallen*.07+key[0])*amp*(1-shrink*.4)
                    lim=m['half']
                    if predicted<m['top']:
                        ball=math.sqrt(max(0,m['radius']**2-(predicted-m['center'])**2))
                        lim+=(ball-lim)*min(1,(m['top']-predicted)/30)
                    lim=max(0,lim-renderer.sprite_radius(m['pitch'],size))
                    expected=m['cx']+max(-lim,min(lim,raw))
                    if abs(current[key][0]-expected)>tolerance:
                        raise AssertionError('同一粒子的横向轨迹不连续')
                if abs(current[key][1] - predicted) > tolerance:
                    raise AssertionError("同一粒子的下落位置不连续")
                if abs(current[key][2] - (vy + gravity * dt)) > tolerance:
                    raise AssertionError("同一粒子的速度不连续")
                self.survivor_steps += 1
            else:
                contact = _surface(before, x) - kill_inset
                if predicted > contact + tolerance:
                    raise AssertionError("粒子尚未触及堆面便消失")
                self.retired.add(key)
                self.contacts += 1
        births = current.keys() - previous.keys()
        if births & self.retired:
            raise AssertionError("已回收的粒子身份在半空复活")
        outlet = before["outlet"]
        lowest = (outlet - max(0.0, spread_ratio) * before["tube_height"]
                  - 60.0 * before["scale"] * dt + 0.5 * gravity * dt * dt)
        for key in births:
            y = current[key][1]
            half=before.get('aperture_half',float('inf'))
            center=before.get('motion',{}).get('cx',0.0)
            radius=0.0
            if 'motion' in before and key in after.get('appearance',{}):
                radius=renderer.sprite_radius(before['motion']['pitch'],after['appearance'][key][2])
            if abs(current[key][0]-center)+radius>half+tolerance:
                raise AssertionError('新粒子的横向出生位置越过出口')
            if not lowest - tolerance <= y <= outlet + tolerance:
                raise AssertionError("新粒子并非从出口出生")
        self.births += len(births)


def _state(rows, surface=0.0):
    return dict(particles=dict(rows), curve=([], []), top=surface,
                outlet=100.0, scale=1.0, tube_height=5.0)


class ContinuityTraceTests(unittest.TestCase):
    def test_birth_footprint_outside_aperture_fails(self):
        key=(.1,.02);before=_state([]);before.update(aperture_half=10,motion=dict(cx=0,pitch=1))
        after=_state([(key,(9,99.7,-45))]);after['appearance']={key:(9,.5,2)}
        with self.assertRaisesRegex(AssertionError,'横向出生'):
            ContinuityTrace().observe(before,after,.01)

    def test_birth_outside_aperture_fails(self):
        before=_state([]);before['aperture_half']=10
        after=_state([((.1,.02),(1000,99.7,-45))])
        with self.assertRaisesRegex(AssertionError,'横向出生'):
            ContinuityTrace().observe(before,after,.01)

    def test_horizontal_teleport_fails(self):
        before=_state([((.1,.02),(0,50,-20))])
        after=_state([((.1,.02),(1000,49.7775,-24.5))])
        with self.assertRaisesRegex(AssertionError,"横向"):
            ContinuityTrace().observe(before,after,.01)

    def test_compaction_does_not_change_identity(self):
        a, b = (0.11, 0.021), (0.22, 0.022)
        before = _state([(a, (0, 0.1, -20)), (b, (1, 50, -20))])
        after = _state([(b, (1, 49.7775, -24.5))])
        trace = ContinuityTrace()
        trace.observe(before, after, .01)
        self.assertEqual((trace.contacts, trace.survivor_steps), (1, 1))

    def test_airborne_removal_fails(self):
        before = _state([((.1, .02), (0, 50, -20))])
        with self.assertRaisesRegex(AssertionError, "尚未触及"):
            ContinuityTrace().observe(before, _state([]), .01)


    def test_new_particle_in_middle_fails(self):
        after = _state([((.1, .02), (0, 50, -20))])
        with self.assertRaisesRegex(AssertionError, "出口出生"):
            ContinuityTrace().observe(_state([]), after, .01)

    def test_outlet_birth_with_partial_step_is_valid(self):
        after = _state([((.1, .02), (0, 99.7, -45))])
        trace = ContinuityTrace()
        trace.observe(_state([]), after, .01)
        self.assertEqual(trace.births, 1)

    def test_pause_keeps_the_same_particle(self):
        state = _state([((.1, .02), (0, 50, -20))])
        ContinuityTrace().observe(state, state, 0)
        moved = _state([((.1, .02), (0, 49, -20))])
        with self.assertRaisesRegex(AssertionError, "位置不连续"):
            ContinuityTrace().observe(state, moved, 0)

    def test_retired_identity_cannot_reappear(self):
        key = (.1, .02)
        trace = ContinuityTrace()
        trace.observe(_state([(key, (0, 0.1, -20))]), _state([]), .01)
        with self.assertRaisesRegex(AssertionError, "复活"):
            trace.observe(_state([]), _state([(key, (0, 99.7, -45))]), .01)

    def test_contact_uses_local_surface(self):
        before = _state([((.1, .02), (-1, 2.0, -20))], surface=10)
        before["curve"] = ([-1, 0, 1], [0, 10, 0])
        with self.assertRaisesRegex(AssertionError, "尚未触及"):
            ContinuityTrace().observe(before, _state([]), .01)


class ParticleVertexTests(unittest.TestCase):
    def pack(self, xs, ys, phases, sizes, pitch=1.7):
        self.assertTrue(hasattr(renderer, "particle_vertices"),
                        "旧整柱纹理没有逐颗粒子的真实顶点，不能通过连续身份契约")
        return renderer.particle_vertices(xs, ys, phases, sizes, pitch)

    def test_every_live_particle_has_its_own_short_quad(self):
        xs, ys = [0.0, 2.0, -7.0], [100.0, 52.5, 1.0]
        vertices = self.pack(xs, ys, [.1, 1.2, 5.7], [1, 2, 1])
        self.assertEqual(vertices.shape, (3, 4, 5))
        self.assertEqual(vertices.dtype, np.float32)
        self.assertTrue(np.isfinite(vertices).all())
        np.testing.assert_allclose(vertices[:, :, :2].mean(axis=1),
                                   np.column_stack((xs, ys)), rtol=0, atol=2e-5)
        spans = np.ptp(vertices[:, :, :2], axis=1)
        self.assertTrue((spans > 0).all())
        self.assertTrue((spans[:, 1] <= 3.0 * spans[:, 0]).all(),
                        "短粒不能重新拉成长条")
        uv = vertices[:, :, 2:4]
        self.assertTrue((np.abs(uv) <= 1.0).all())
        self.assertTrue((np.ptp(uv, axis=1) > 0).all())

    def test_identity_survives_compaction_and_position_change(self):
        first = self.pack([1, 2, 3], [90, 80, 70], [.2, .4, .6], [1, 2, 1])
        second = self.pack([2.1, 3.1], [75, 65], [.4, .6], [2, 1])
        np.testing.assert_array_equal(first[1:, :, 2:], second[:, :, 2:])
        np.testing.assert_allclose(second[:, :, :2].mean(axis=1),
                                   [[2.1, 75], [3.1, 65]], rtol=0, atol=2e-5)

    def test_single_particle_cannot_be_sieved_away(self):
        for phase in np.linspace(0, math.tau, 31):
            for depth in (99.5, 55, .5):
                result = self.pack([10], [depth], [phase], [1])
                self.assertEqual(result.shape, (1, 4, 5))

    def test_empty_input_produces_no_phantom_grain(self):
        result = self.pack([], [], [], [])
        self.assertEqual(result.shape, (0, 4, 5))

    def test_repeated_render_does_not_move_or_recolour_paused_grains(self):
        args = ([1, 2], [90, 30], [.2, .4], [1, 2])
        np.testing.assert_array_equal(self.pack(*args), self.pack(*args))


class MoundSurfaceTests(unittest.TestCase):
    @staticmethod
    def quad(x0, y0, x1, y1, floor=0):
        return [x0, y0, 0, 0, x1, y1, 1, 0,
                x1, floor, 1, 1, x0, floor, 0, 1]

    def sample(self, xs, vertices, floor):
        self.assertTrue(hasattr(renderer, "mound_surface_at"),
                        "沙粒需要按实际堆面遮挡，不能越过沙堆表面绘制")
        return renderer.mound_surface_at(xs, vertices, floor)

    def test_samples_actual_piecewise_surface(self):
        vertices = self.quad(0, 10, 10, 20) + self.quad(10, 20, 20, 5)
        result = self.sample([0, 5, 10, 15, 20], vertices, 2)
        np.testing.assert_allclose(result, [10, 15, 20, 12.5, 5])

    def test_zero_width_quads_do_not_distort_surface(self):
        vertices = (self.quad(0, 10, 10, 20)
                    + self.quad(0, 999, 0, 999)
                    + self.quad(10, 20, 20, 5)
                    + [0] * 32)
        result = self.sample([0, 5, 10, 15, 20], vertices, 2)
        np.testing.assert_allclose(result, [10, 15, 20, 12.5, 5])

    def test_empty_or_degenerate_surface_uses_floor(self):
        for vertices in ([], [0] * 32):
            with self.subTest(vertices=vertices):
                result = self.sample([0, 5, 10], vertices, 2.5)
                np.testing.assert_array_equal(result, [2.5, 2.5, 2.5])


class ParticleFallbackTests(unittest.TestCase):
    def test_mesh_creation_failure_restores_attached_line_renderer(self):
        """首个 Mesh 失败晚于 shader 成功，不能留下脱离画布的 Line 池。"""
        class Canvas:
            def __init__(self):
                self.children = []

            def remove(self, item):
                self.children.remove(item)

            def insert(self, index, item):
                self.children.insert(index, item)

        class Context:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Widget:
            def __init__(self):
                self._cx = self._R_inner = self._upper_sand_bot = 1.0
                self._taper = {"in_pts": [(0.0, 1.0)]}
                self._sand_material = None
                self.canvas = Canvas()
                self.reserved = False

            def _build_dynamic_canvas(self):
                self.groups = [object(), object()]
                self._stream_pools = {i: (g, None, [])
                                      for i, g in enumerate(self.groups)}
                self.canvas.children[:] = self.groups

            def _draw_stream(self):
                self.line_drawn = True

            def _reserve_stream_lines(self):
                self.reserved = True

        def fail_mesh(*args, **kwargs):
            raise RuntimeError("synthetic Mesh allocation failure")

        graphics = types.ModuleType("kivy.graphics")
        graphics.Color = lambda *args: None
        graphics.Callback = lambda *args, **kwargs: None
        graphics.Mesh = fail_mesh
        opengl = types.ModuleType("kivy.graphics.opengl")
        opengl.GL_CURRENT_PROGRAM = 0x8B8D
        opengl.glGetIntegerv = lambda name: [1]
        opengl.glGetAttribLocation = lambda program, name: {
            b"vPosition": 0, b"vTexCoords0": 1,
            b"vTone": 2, b"vSurface": 3,
        }.get(name, -1)
        opengl.glEnableVertexAttribArray = lambda location: None
        material = types.ModuleType("sand_flow_material")
        material.GRAIN_FRAGMENT_HEADER = ""
        material.SandFlowContext = Context
        with patch.dict(sys.modules, {"kivy.graphics": graphics,
                                      "kivy.graphics.opengl": opengl,
                                      "sand_flow_material": material}), \
                patch.dict(os.environ, {"HG_FLOW_FORCE_FAIL": ""}):
            renderer.install(Widget)
            widget = Widget()
            widget._build_dynamic_canvas()
            self.assertEqual(widget.flow_renderer, "line_pool")
            self.assertTrue(widget.reserved)
            self.assertIsNone(widget._short_flow_context,
                              "Mesh 失败后仍会进入新渲染器，Line 回退没有接管")
            self.assertEqual(widget.canvas.children, widget.groups,
                             "回退 Line 池已从实际画布移除")
            widget._draw_stream()
            self.assertTrue(widget.line_drawn)


if __name__ == "__main__":
    unittest.main()
