# -*- coding: utf-8 -*-
"""峰值负载下 **物理/redraw 的墙钟耗时**随各部件数量的变化 —— 不用 cProfile。

为什么不用 cProfile: 它按**函数调用**计开销, 本项目每帧上百万次微调用 ⇒ 放大好几倍,
今天已因此把"−49%"错报成设备上的"−9%"。墙钟只回答"这一帧到底花了多少"。

跑法: python tools/_probe_frame_cost.py [周期=15]
      HG_SPLASH_CAP=0 python tools/_probe_frame_cost.py 15   # 砍掉飞溅(对照臂)
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
CAP = int(os.environ.get("HG_SPLASH_CAP", "-1"))


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_FLOW_RENDERER"] = "texture"     # 与设备同一套渲染器
    sys.argv = [sys.argv[0], "_cost"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None

    stats = {"phys": [], "redraw": [], "pn": [], "sp": []}
    # ⚠️ 桌面窗口比真机矮 ⇒ 15s 档只有 ~580 颗, **低于 `_NUMPY_MIN=800`**,
    #    于是渲染向量化路径一次都走不到, A/B 两个臂会量成完全一样(踩过)。
    #    要量向量化路径就把它压下来 —— 两条臂都压, 相对比较仍然有效。
    _nmin = os.environ.get("HG_NUMPY_MIN")
    if _nmin:
        m._NUMPY_MIN = int(_nmin)
        print("  (已把 _NUMPY_MIN 覆写成 %s)" % _nmin)
    orig_phys = m.HourglassWidget.update_particles
    orig_redraw = m.HourglassWidget.redraw

    def phys(self, dt):
        t0 = time.perf_counter()
        r = orig_phys(self, dt)
        stats["phys"].append((time.perf_counter() - t0) * 1000)
        # ⚠️ 原来是 `del self.splashes[CAP:]` —— `splashes` 现在是 property, 每次访问都
        #    新建一批 dict, 那个 del 删的是**临时 list**, 静默无效。
        #    等价写法: 截存活数(保留前 CAP 个, 与原来同序)。
        if CAP >= 0 and self._sn > CAP:
            self._sn = CAP
        return r

    def redraw(self):
        t0 = time.perf_counter()
        r = orig_redraw(self)
        stats["redraw"].append((time.perf_counter() - t0) * 1000)
        stats["pn"].append(self.pn)
        stats["sp"].append(len(self.splashes))
        return r

    m.HourglassWidget.update_particles = phys
    m.HourglassWidget.redraw = redraw

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            hg = self.hourglass
            hg.set_duration(PERIOD)
            hg._rebuild_height_table()
            hg.reset()
            hg.toggle()
            Clock.schedule_once(lambda dt: self.stop(), PERIOD)

    P().run()
    return stats


if __name__ == "__main__":
    st = run()
    n = min(len(st["phys"]), len(st["redraw"]))
    if n < 30:
        print("  采样太少"); raise SystemExit
    # 取后半段(稳态)均值
    a = n // 2
    def avg(k):
        return sum(st[k][a:n]) / (n - a)
    print("")
    print("  === frame cost (period %gs, desktop 400x800, texture) ===" % PERIOD)
    print("  HG_SPLASH_CAP = %s" % ("不限" if CAP < 0 else CAP))
    print("  steady: particles %6.0f / splashes %6.0f" % (avg("pn"), avg("sp")))
    print("  physics  %6.2f ms/frame" % avg("phys"))
    print("  redraw   %6.2f ms/frame" % avg("redraw"))
    print("  SUM      %6.2f ms/frame" % (avg("phys") + avg("redraw")))
    print("")
