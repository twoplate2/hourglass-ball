# -*- coding: utf-8 -*-
"""**末段沙流 ↔ 沙堆尖之间的空隙** —— 用户 2026-10-09 报:「50 秒档最后 3 秒, 很多帧里
沙流和下面沙堆的尖之间有明显空隙」。

## 量什么(几何口径, 不读图 —— 读图的坐标映射很容易错)

逐帧记三个绝对 y(Kivy y 向上):
  * `mound_top`  = `get_mound_top_y()` = **粒子碰撞面**(中轴处的接触高度)
  * `drawn_top`  = 绘制用的同一条 `contact(0, apex)` + 球内底(应当 == mound_top)
  * `ink_low`    = `min(py[:pn])` = **实际画出来的最低沙墨**(粒子按 [y, y+trail] 画)

空隙 = `ink_low − drawn_top`(Kivy y: 沙墨最低点在沙面上方多少 px)。
再数一条中轴带里的"墨": 落在 [drawn_top, drawn_top+80] 这段窗口里的粒子数。

跑法: python tools/_probe_mound_gap.py [周期] [看的秒数]
"""
import os
import statistics as st
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
WINDOW = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
SAVE = os.environ.get("HG_GAP_SHOTS", "1") == "1"

with tempfile.TemporaryDirectory(prefix="mound-gap-") as home:
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
            Window.system_size = (1904, 2890)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD)
            w.reset(); w._rebuild_height_table()
            w.toggle()
            dt = 1.0 / 60.0
            target = max(0.0, PERIOD - WINDOW)
            for _ in range(int(target / dt)):
                now[0] += dt
                w.tick(dt)
            shot_dir = ROOT / "benchmark_logs" / "_vid" / "gap"
            shot_dir.mkdir(parents=True, exist_ok=True)
            print("")
            print("  周期 %.0fs  末段 %.0fs  球内底 %.1f  出口 %.1f  画布 h=%.0f"
                  % (PERIOD, WINDOW, w._lower_sand_bot,
                     2 * w._neck_y - w._taper["y_bot"], w.height))
            print("")
            print("  %7s %10s %10s %10s %9s %8s %8s %8s" %
                  ("elapsed", "apex", "mound_top", "ink_low", "空隙px", "近墨数", "中轴墨", "粒子数"))
            rows = []
            n_step = int(WINDOW / dt)
            for i in range(n_step):
                now[0] += dt
                w.tick(dt)
                apex = w._mound_apex()
                mtop = w.get_mound_top_y()
                pn = w.pn
                if pn:
                    py = w.py[:pn]
                    ink = min(float(v) for v in py)
                    near = sum(1 for v in py if mtop <= float(v) <= mtop + 80.0)
                    axis = sum(1 for j in range(pn)
                               if abs(float(w.px[j]) - w._cx) <= 6.0
                               and mtop <= float(py[j]) <= mtop + 120.0)
                else:
                    ink, near, axis = mtop, 0, 0
                gap = ink - mtop
                rows.append((w.elapsed, apex, mtop, ink, gap, near, axis, pn))
                if i % 3 == 0:
                    print("  %7.2f %10.1f %10.1f %10.1f %9.1f %8d %8d %8d"
                          % (w.elapsed, apex, mtop, ink, gap, near, axis, pn))
                if SAVE and i % 6 == 0:
                    w.redraw()
                    w.export_to_png(str(shot_dir / ("gap_%.2f.png" % w.elapsed)))
            gaps = [r[4] for r in rows]
            nears = [r[5] for r in rows]
            print("")
            print("  == 末段 %.0fs 的 %d 帧汇总 ==" % (WINDOW, len(rows)))
            print("  空隙(ink_low − mound_top): 中位 %.1f  p90 %.1f  max %.1f  px"
                  % (st.median(gaps), sorted(gaps)[int(len(gaps) * 0.9)], max(gaps)))
            print("  空隙 > 2px 的帧: %d / %d (%.0f%%)"
                  % (sum(1 for g in gaps if g > 2), len(gaps),
                     100.0 * sum(1 for g in gaps if g > 2) / len(gaps)))
            print("  空隙 > 5px 的帧: %d / %d (%.0f%%)"
                  % (sum(1 for g in gaps if g > 5), len(gaps),
                     100.0 * sum(1 for g in gaps if g > 5) / len(gaps)))
            print("  [沙面, 沙面+80] 里一颗墨都没有的帧: %d / %d (%.0f%%)"
                  % (sum(1 for n in nears if n == 0), len(nears),
                     100.0 * sum(1 for n in nears if n == 0) / len(nears)))
            print("  近墨数 中位 %.0f  p10 %.0f  min %d" %
                  (st.median(nears), sorted(nears)[int(len(nears) * 0.1)], min(nears)))
            print("")
            print("  图 -> %s" % shot_dir.relative_to(ROOT))
            self.stop()

    Probe().run()
