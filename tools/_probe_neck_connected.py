# -*- coding: utf-8 -*-
"""颈部**直筒段到底有没有被填沙** —— 2026-10-07 用户报「颈部的沙子和其他地方的沙子分成 2 团」。

## 被查的算式（`redraw` 里那段，main.py:6325-6358）

    outlet    = 2*neck_y − taper['y_bot']        # 直筒**下端**（= 出口）
    inlet     = taper['y_bot']                   # 直筒**上端**
    connected = bool(side and side[-1][1] <= outlet + 1e-6)

`side` = `_neck_sand_side()` 的返回，而它的轮廓点来自 `taper['in_pts']`（**上喇叭口**），
最后一点是 `y_bot` = **直筒上端**。`connected` 要求**末点 ≤ 出口**。

⇒ 若填充分支不 append 出口点，则 `y_bot > outlet` ⇒ **connected 恒 False**，而 False 时：
  · 最后一格四边形**不**延伸到 `fade_top`（直筒上半没沙）
  · `_neck_solid_rect` / `_neck_fade_rect`（直筒的沙填充矩形）**根本不被赋值** ⇒ 尺寸停在初始值
⇒ **整条直筒一个像素的沙都没有** —— 上球那团与下球沙堆之间就这么断开了。

跑法: python tools/_probe_neck_connected.py
判据: `connected` 一栏在**正常运行的绝大多数时刻**必须是 True；本探针同时打印
      直筒两个矩形的 size（False 时它们不该是 0）。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

from kivy.clock import Clock                     # noqa: E402
from kivy.core.window import Window              # noqa: E402
import main as m                                 # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *a, **kw: None
m.HourglassWidget._make_completion_sound = lambda *a, **kw: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 50.0}
m.HourglassWidget.save_config = lambda *_: None

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
TIMES = [0.3, 1.0, 10.0, 30.0, 44.0, 48.0, 49.5,
         PERIOD - 0.2, PERIOD - 0.05, PERIOD]


class Probe(m.HourglassApp):
    def on_start(self):
        Window.size = (400, 800)
        Clock.schedule_once(self.go, 0.9)

    def go(self, _dt):
        hg = self.hourglass
        hg.set_duration(PERIOD)
        print("")
        print("  周期 %.0fs  出口/直筒几何来自同一份 _taper" % PERIOD)
        print("   t(s)   side点数  首点y    末点y    inlet    outlet   connected  直筒矩形 size")
        bad = 0
        pinned = 0
        for t in TIMES:
            hg.reset()
            hg.elapsed = t
            hg._done_at = None
            side = hg._neck_sand_side()
            outlet = 2 * hg._neck_y - hg._taper["y_bot"]
            inlet = hg._taper["y_bot"]
            conn = bool(side and side[-1][1] <= outlet + 1e-6)
            size = tuple(round(v, 1) for v in hg._neck_solid_rect.size)
            if not conn:
                bad += 1
            print("  %6.2f   %3d     %7.2f  %7.2f  %7.2f  %7.2f   %-9s %s"
                  % (t, len(side), side[0][1] if side else 0.0,
                     side[-1][1] if side else 0.0, inlet, outlet, conn, size))
        print("")
        print("  ⇒ 直筒无沙(connected=False): %d/%d 个时刻" % (bad, len(TIMES)))
        print("     正常运行的时刻应当是 0 —— 只有起跑注满的最初 %.2fs 和归零后排空允许断开。"
              % hg._neck_fill_time)
        # ---- 末段排空的**动边**: 出口必须钉住(`末点y == outlet`), 顶边往下退 ----
        # 负对照: 把 `draining` 改回只看 `_done_at` ⇒ 末点 y 会**升到 outlet 之上** ⇒ 翻红。
        print("")
        print("  == 末段(最后 %.2fs)的动边: 出口钉住 vs 顶边往下退 ==" % hg._neck_fill_time)
        prev = None
        for t in [PERIOD - 0.20, PERIOD - 0.10, PERIOD - 0.02]:
            hg.reset()
            hg.elapsed = t
            hg._done_at = None
            side = hg._neck_sand_side()
            outlet = 2 * hg._neck_y - hg._taper["y_bot"]
            if not side:
                print("  %6.2f  沙柱已空" % t)
                continue
            print("  %6.2f  首点y %7.2f  末点y %7.2f  (出口 %7.2f)  末点-出口 %+6.2f  %s"
                  % (t, side[0][1], side[-1][1], outlet, side[-1][1] - outlet,
                     "出口钉住 ✓" if abs(side[-1][1] - outlet) < 0.01 else "**出口被让开了**"))
            if prev is not None and side[0][1] > prev + 1e-6:
                print("          ↑ 首点 y **上升**了(顶边在往上长) —— 与排空相反")
            prev = side[0][1]
        self.stop()


Probe().run()
