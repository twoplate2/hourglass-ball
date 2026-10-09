# -*- coding: utf-8 -*-
"""末段阶梯: 「已经没有沙在流, 沙堆却还在长」—— 50s 档最后 5s 的逐秒状态表。

用户 2026-10-09 报: 「在较长时间的计时后, 在最后的几秒内, 已经没有沙子在流动,
但是沙堆在不断变高, 然后填满, 这个不合理」。

本探针只读**状态量**(不读图), 每秒打一行:
  t        当前时刻
  upper    上球沙面高度(px, `_upper_sand_height_px`)
  mound    下沙堆高度(px, `_mound_height`)
  pn       在途粒子数
  col      颈部沙柱是否存在(`_neck_sand_side` 返回 >2 个节点)
  fallen   `_effective_fallen()`
判据: **upper 已经到 0(或 pn 已经到 0) 而 mound 还在长** ⇒ 就是用户看到的那条。

跑法: python tools/_probe_tail_ladder.py [周期] [起始秒] [结束秒]
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
T0 = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
T1 = float(sys.argv[3]) if len(sys.argv) > 3 else PERIOD
WW, HH, DENS = 1080, 1920, "2.75"
os.environ.setdefault("KIVY_METRICS_DENSITY", DENS)
os.environ["HG_FLOW_RENDERER"] = "texture"

with tempfile.TemporaryDirectory(prefix="tail-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (WW, HH)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation()
            self.root.do_layout()
            self.root._anchor.do_layout()
            self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD)
            w.reset()
            w._rebuild_height_table()
            w.toggle()
            dt = 1.0 / 60.0
            nxt = T0
            print("  周期 %.1fs  t=%.1f..%.1f   平板口径 %dx%d" % (PERIOD, T0, T1, WW, HH))
            print("    t   | upper | mound |   pn | col节点 | fallen | 说明")
            while w.elapsed < min(T1, PERIOD) - 1e-9:
                now[0] += dt
                w.tick(dt)
                if w.elapsed >= nxt - 1e-9:
                    try:
                        up = w._upper_sand_height_px()
                    except Exception:
                        up = float("nan")
                    try:
                        mo = w._mound_height()
                    except Exception:
                        mo = float("nan")
                    try:
                        side = w._neck_sand_side()
                        col = len(side)
                    except Exception:
                        col = -1
                    try:
                        fa = w._effective_fallen()
                    except Exception:
                        fa = float("nan")
                    note = ""
                    if up <= 0.5 and mo < 0.999:
                        note = "<<< 上球空了但沙堆还在长"
                    print("  %5.2f | %5.1f | %5.1f | %4d | %6d | %.3f | %s"
                          % (w.elapsed, up, mo, getattr(w, "pn", -1), col, fa, note))
                    _od = ROOT / "benchmark_logs" / "_vid" / "tail"
                    _od.mkdir(parents=True, exist_ok=True)
                    try:
                        w.export_to_png(str(_od / ("tail_%05.2f.png" % w.elapsed)))
                    except Exception as _e:
                        print("       (导出失败 %s)" % _e)
                    nxt += max(0.25, (T1 - T0) / 30.0)
            self.stop()
