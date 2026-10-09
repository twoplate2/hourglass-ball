# -*- coding: utf-8 -*-
"""**射程下限(现在 1/3)该取多少** —— 把三个候选放到同一个标尺上量(2026-10-09)。

用户问: 「最长 100% 没问题, 最短 33% 是否合理? 改 1/4 或 1/5 呢?」

这个 `1/3` 是 `range_fraction = 1/3 + 2/3·blend` 里的下限, **没有物理锚点**(纯口味)。
本探针给它找两个**可量的参照**, 都用**平板口径**(真机):

 ① **颗粒直径** —— 飞溅颗粒本身多大(`size = round(SPLASH_PX_BASE·R_inner/140)`)。
    射程若只剩几颗粒径, 读起来就不是"溅"而是"抖"。
 ② **沙流自身的横向散布** —— 这是**真正的底线**: 飞溅的射程必须明显超过沙流自己有多宽,
    否则它在画面里根本分不出来(沙流本来就在抖)。逐帧量主流颗粒的 |x − cx| 分位。

跑法: python tools/_probe_range_floor.py
"""
import math
import os
import tempfile
import random
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

import statistics as st                          # noqa: E402

with tempfile.TemporaryDirectory(prefix="range-floor-") as tempfile_home:
    os.environ.update(KIVY_HOME=tempfile_home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": 60000}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])

    TIERS = [5.0, 30.0, 600.0, 3600.0, 36000.0]
    FLOORS = [1.0 / 3.0, 0.25, 0.2]

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (1904, 2890)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w._rebuild_height_table()
            base5 = w._neck_width_for_duration(5.0) - w._ow
            narrow = max(1.0, w._neck_width_for_duration(36000.0) - w._ow)
            px_head = m.SPLASH_PX_BASE * max(1.0, w._R_inner / 140.0)
            grain = max(1, round(px_head))
            print("")
            print("  平板口径  R=%.0f  R_inner=%.0f  描边 %.0f  最粗内宽 %.1fpx  最细内宽 %.1fpx"
                  % (w._R, w._R_inner, w._ow, base5, narrow))
            print("  **飞溅颗粒本身 ≈ %d×%d px**(SPLASH_SIZE_MIX 只有一项)" % (grain, grain))
            print("")
            print("  == ① 三个下限在各档给出的射程(以最粗档 =100% 计) ==")
            print("  %9s %7s %8s | %8s %8s %8s | %s" %
                  ("时长", "内宽px", "wf", "1/3", "1/4", "1/5", "换算成颗粒直径(1/3 / 1/4 / 1/5)"))
            rows = {}
            for d in TIERS:
                t_in = max(1.0, w._neck_width_for_duration(d) - w._ow)
                wf = max(0.0, min(1.0, (t_in - narrow) / max(1e-9, base5 - narrow)))
                s = wf * wf * (3.0 - 2.0 * wf)
                vals = [f + (1.0 - f) * s for f in FLOORS]
                rows[d] = vals
                # 射程(px) = 最粗档射程 × 因子; 最粗档实测 p50(桌面 174px 内半径) = 29.1px
                # ⇒ 换到平板(内半径 %.0f) 要乘比例
                k = w._R_inner / 174.0
                p50 = [29.1 * k * v for v in vals]
                print("  %9s %7.1f %8.3f | %7.0f%% %7.0f%% %7.0f%% | %.0fpx(%.1f) / %.0fpx(%.1f) / %.0fpx(%.1f)"
                      % ("%gs" % d, t_in, wf, 100 * vals[0], 100 * vals[1], 100 * vals[2],
                         p50[0], p50[0] / grain, p50[1], p50[1] / grain, p50[2], p50[2] / grain))
            print("")
            print("  == ② 沙流自身的横向散布(真正的底线: 飞溅必须明显宽过它) ==")
            print("  %9s %10s %10s %10s | %s" %
                  ("时长", "主流p90|dx|", "主流max|dx|", "飞溅p50射程", "射程 / 流散布"))
            for d in TIERS:
                w.set_duration(d)
                w.reset()
                random.seed(23)
                w.toggle()
                lat = []
                for _ in range(200):
                    now[0] += 1.0 / 60.0
                    w.tick(1.0 / 60.0)
                    if w.pn:
                        lat.append(max(abs(float(v) - w._cx) for v in w.px[:w.pn]))
                lat.sort()
                p90 = lat[int(len(lat) * 0.9)] if lat else 0.0
                mx = lat[-1] if lat else 0.0
                k = w._R_inner / 174.0
                p50 = 29.1 * k * rows[d][0]
                print("  %9s %10.1f %10.1f %10.1f | **%.1f 倍**(用 1/3); 1/4 时 %.1f 倍; 1/5 时 %.1f 倍"
                      % ("%gs" % d, p90, mx, p50, p50 / max(1e-9, p90),
                         p50 * rows[d][1] / rows[d][0] / max(1e-9, p90),
                         p50 * rows[d][2] / rows[d][0] / max(1e-9, p90)))
            print("")
            print("  ⚠️ 判据(我提出, **未标定**): 射程/流散布 至少要 > 2 才可能读成「溅」。")
            self.stop()

    Probe().run()
