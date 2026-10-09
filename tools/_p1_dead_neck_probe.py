# -*- coding: utf-8 -*-
"""1号专家(证伪者)一次性探针: 颈部颗粒层在 GPU 材质路径下是不是"挂着的死层"。

三种臂(命令行第一个参数):
  default  : 桌面默认环境(不设任何 HG_*) —— 主张说的 960 条
  flat     : HG_SAND_MATERIAL=flat —— 主张说的"回退路径下它是活的"
  batchidle: 安卓代理(HG_SPLASH/NECK/FLOW_RENDERER 全 batch/texture)+ 纯闲置 3s
             —— 看 neck 族的部件会不会被预热建出来(65 还是 129)

default/flat 臂输出:
  A) 材质路径状态: _sand_material / len(_sand_flow_contexts)
  B) _draw_neck_grains 调用计数(运行 1.5s)
  C) 池子实际点数(320 条 Line 里有几条 points 非空)
  D) 指令普查: neck_grain 族条数 + 全画布总条数(逐类型)
  E) 像素消融: 冻结状态后 [在] vs [摘] 逐像素差(控制臂 [在] vs [在2] 必须 0)
  F) 时间消融: Window.on_draw, 挂族 2s vs 摘族 2s
"""
import collections
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ARM = sys.argv[1] if len(sys.argv) > 1 else "default"
os.environ["KIVY_NO_ARGS"] = "1"
os.environ["KIVY_NO_FILELOG"] = "1"
os.environ["KIVY_METRICS_DENSITY"] = "1.75"
os.environ["KIVY_METRICS_FONTSCALE"] = "1"
if ARM == "flat":
    os.environ["HG_SAND_MATERIAL"] = "flat"
if ARM == "batchidle":
    os.environ["HG_FLOW_RENDERER"] = "texture"
    os.environ["HG_SPLASH_RENDERER"] = "batch"
    os.environ["HG_NECK_RENDERER"] = "batch"
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402


def walk(node, ty, fam, top):
    for ch in getattr(node, "children", ()):
        name = type(ch).__name__
        ty[name] += 1
        fam[top][name] += 1
        walk(ch, ty, fam, top)


def census(hg):
    ty = collections.Counter()
    fam = collections.defaultdict(collections.Counter)
    grain = getattr(hg, "_neck_grain_group", None)
    for i, ch in enumerate(hg.canvas.children):
        top = "neck_grain_family" if ch is grain else "%s#%d" % (type(ch).__name__, i)
        fam[top][type(ch).__name__] += 1
        walk(ch, ty, fam, top)
    total = sum(ty.values())
    return ty, fam, total


def run_default_like():
    import main as m
    from kivy.app import App
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": float(sys.argv[2] if len(sys.argv) > 2 else 60)}
    m.HourglassWidget.save_config = lambda *_: None

    calls = {"n": 0}
    orig_draw = m.HourglassWidget._draw_neck_grains

    def counting(self, side):
        calls["n"] += 1
        return orig_draw(self, side)

    m.HourglassWidget._draw_neck_grains = counting

    box = {}
    samples = {"on": [], "off": [], "on2": []}
    phase = {"p": 0}
    orig_on_draw = Window.on_draw

    def probed(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig_on_draw(*a, **k)
        finally:
            if phase["p"]:
                samples[("", "on", "off", "on2")[phase["p"]]].append(
                    (time.perf_counter() - t0) * 1000.0)

    Window.on_draw = probed

    shots = {}

    def grab(name):
        width, height = map(int, Window.size)
        px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
        img = Image.frombytes("RGBA", (width, height), px)
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
        shots[name] = np.asarray(img).astype(int)

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.step1, 1.2)

        def step1(self, _dt):
            hg = self.hourglass
            print("ARM=%s" % ARM)
            print("sand_material is not None : %s" % (hg._sand_material is not None))
            print("len(_sand_flow_contexts)  : %d" % len(hg._sand_flow_contexts or ()))
            print("neck_renderer             : %s" % getattr(
                m.HourglassWidget, "neck_renderer", "line(default-class-attr?)"))
            pool = hg._neck_grain_pool
            nonempty = sum(1 for _c, ln in pool if len(ln.points))
            print("pool=%d  non-empty Line=%d  grain_count=%d  calls=%d"
                  % (len(pool), nonempty, hg._neck_grain_count, calls["n"]))
            ty, fam, total = census(hg)
            print("canvas TOTAL=%d" % total)
            c = fam["neck_grain_family"]
            print("neck_grain family=%d  %s" % (sum(c.values()), dict(c)))
            # 跑 1.5s
            hg.toggle()
            Clock.schedule_once(self.step2, 1.5)

        def step2(self, _dt):
            hg = self.hourglass
            pool = hg._neck_grain_pool
            nonempty = sum(1 for _c, ln in pool if len(ln.points))
            print("after 1.5s run: calls(_draw_neck_grains)=%d  grain_count=%d  nonempty=%d"
                  % (calls["n"], hg._neck_grain_count, nonempty))
            if ARM == "flat":
                # 活层: 冻结 + 摘族应当改像素
                phase["p"] = 1  # 跑着量 2s on_draw
                Clock.schedule_once(self.freeze_and_shots, 2.0)
            else:
                Clock.schedule_once(self.freeze_and_shots, 0.05)

        def freeze_and_shots(self, _dt):
            hg = self.hourglass
            Clock.unschedule(hg.tick)
            # 冻结时间: flares/尘埃等按 perf_counter 过期, 不冻时间控制臂就不是 0
            import types
            fake = types.SimpleNamespace(
                perf_counter=lambda: 123456.0,
                monotonic=lambda: 123456.0,
                time=lambda: 123456.0)
            m.time = fake
            hg.redraw()
            Clock.schedule_once(lambda d: grab("A"), 0.15)
            Clock.schedule_once(lambda d: (hg.redraw(), None), 0.30)
            Clock.schedule_once(lambda d: grab("A2"), 0.45)
            Clock.schedule_once(self.remove_and_shots, 0.6)

        def remove_and_shots(self, _dt):
            hg = self.hourglass
            hg.canvas.remove(hg._neck_grain_group)
            hg.redraw()
            Clock.schedule_once(lambda d: grab("B"), 0.25)
            Clock.schedule_once(self.metrics, 0.5)

        def metrics(self, _dt):
            a, a2, b = shots["A"], shots["A2"], shots["B"]
            d_ctl = np.abs(a - a2).max(axis=2)
            d_ab = np.abs(a - b).max(axis=2)
            print("PIXELS control  A vs A2 : changed=%d max=%d"
                  % (int((d_ctl > 0).sum()), int(d_ctl.max())))
            print("PIXELS grains   A vs B  : changed=%d max=%d mean=%.2f"
                  % (int((d_ab > 0).sum()), int(d_ab.max()),
                     float(d_ab[d_ab > 0].mean()) if (d_ab > 0).any() else 0.0))
            # 时间消融: 物理保持冻结, 只用 redraw 驱动, 粒子数恒定 ⇒ 无负载漂移。
            # 三段三明治: 挂(p1) -> 摘(p2) -> 挂(p3), p1/p3 差就是纯漂移。
            hg = self.hourglass
            hg.canvas.add(hg._neck_grain_group)   # 先挂回
            # 交替消融: 挂/摘 × 3 轮, 漂移在配对里抵消
            self._seq = [(2, "remove"), (1, "add"), (2, "remove"), (1, "add"),
                         (2, "remove"), (1, "add")]
            drv = lambda dt: hg.redraw()
            self._drv = drv
            Clock.schedule_interval(drv, 1 / 60.0)
            self._cost_step()

        def _cost_step(self, _dt=None):
            hg = self.hourglass
            if not self._seq:
                self._cost_done()
                return
            p, act = self._seq.pop(0)
            if act == "add":
                hg.canvas.add(hg._neck_grain_group)
            else:
                hg.canvas.remove(hg._neck_grain_group)
            phase["p"] = p
            Clock.schedule_once(self._cost_step, 2.0)

        def _cost_done(self):
            phase["p"] = 0
            Clock.unschedule(self._drv)
            hg = self.hourglass
            hg.canvas.add(hg._neck_grain_group)
            on = samples.get("on") or []
            off = samples.get("off") or []
            if len(on) >= 40 and len(off) >= 40:
                mo, mf = float(np.median(on)), float(np.median(off))
                po = float(np.percentile(on, 90))
                pf = float(np.percentile(off, 90))
                print("COST on_draw 挂族 中位 %.3f / p90 %.3f ms (n=%d) | 摘族 中位 %.3f / p90 %.3f ms (n=%d)"
                      % (mo, po, len(on), mf, pf, len(off)))
                print("COST Δ中位 %+.3f ms | Δp90 %+.3f ms (3 对交错窗口)" % (mo - mf, po - pf))
                for i in range(0, min(len(on), len(off)), 60):
                    pass
            else:
                print("COST 样本不足: on=%d off=%d" % (len(on), len(off)))
            self.stop()

    P().run()


def run_batch_idle():
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 15}
    m.HourglassWidget.save_config = lambda *_: None

    calls = {"n": 0}
    orig_draw = m.HourglassWidget._draw_neck_grains  # 已装的 draw_neck_batches(batch 臂)

    def counting(self, side):
        calls["n"] += 1
        return orig_draw(self, side)

    m.HourglassWidget._draw_neck_grains = counting

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.report1, 1.5)

        def report1(self, _dt):
            hg = self.hourglass
            print("ARM=batchidle @1.5s idle")
            print("sand_material=%s  flow_contexts=%d" % (
                hg._sand_material is not None, len(hg._sand_flow_contexts or ())))
            ty, fam, total = census(hg)
            print("canvas TOTAL=%d" % total)
            for k in ("neck_grain_family",):
                print("%s: %s" % (k, dict(fam.get(k, {}))))
            nb = getattr(hg, "_neck_batches", None)
            if nb is None:
                print("_neck_batches = None (batch 未装)")
            else:
                parts = [len(b.parts) for _c, b in nb]
                print("neck batches=%d  parts per batch=%s (非零=已预热/建过块)"
                      % (len(nb), sorted(set(parts))))
                nonempty_grp = sum(1 for _c, b in nb if b.parts)
                print("已建块的颈批=%d / %d" % (nonempty_grp, len(nb)))
            # 找到 neck context(按类型找 RenderContext 且 children 里有 32 组的那个)
            for ch in hg.canvas.children:
                if type(ch).__name__ == "RenderContext" and ch is getattr(hg, "_neck_context", None):
                    c = collections.Counter()
                    walk(ch, c, collections.defaultdict(collections.Counter), "x")
                    print("neck_context 递归条数=%d  %s" % (sum(c.values()), dict(c)))
            # 再闲置 3s 看预热会不会把 neck 块建出来
            Clock.schedule_once(self.report2, 3.0)

        def report2(self, _dt):
            hg = self.hourglass
            nb = getattr(hg, "_neck_batches", None)
            if nb is not None:
                parts = [len(b.parts) for _c, b in nb]
                print("@4.5s idle: 已建块的颈批=%d / %d  parts set=%s"
                      % (sum(1 for p in parts if p), len(nb), sorted(set(parts))))
            ch = getattr(hg, "_neck_context", None)
            if ch is not None:
                c = collections.Counter()
                walk(ch, c, collections.defaultdict(collections.Counter), "x")
                print("@4.5s neck_context 递归条数=%d  %s" % (sum(c.values()), dict(c)))
            # 跑 1.5s(真实 tick), 数 batch 臂下 _draw_neck_grains(已换成 draw_neck_batches)被调几次
            hg.toggle()
            Clock.schedule_once(self.report3, 1.5)

        def report3(self, _dt):
            hg = self.hourglass
            nb = getattr(hg, "_neck_batches", None)
            nonempty_parts = sum(1 for _c, b in (nb or ()) if b.parts and b.parts[0][0].indices)
            print("after 1.5s run (batch arm, material path): calls=%d  grain_count=%d  "
                  "neck批非空索引块=%d  running=%s"
                  % (calls["n"], getattr(hg, "_neck_grain_count", -1), nonempty_parts, hg.running))
            ch = getattr(hg, "_neck_context", None)
            if ch is not None:
                c = collections.Counter()
                walk(ch, c, collections.defaultdict(collections.Counter), "x")
                print("after run neck_context 递归条数=%d  %s" % (sum(c.values()), dict(c)))
            self.stop()

    P().run()


if ARM == "batchidle":
    run_batch_idle()
else:
    run_default_like()
