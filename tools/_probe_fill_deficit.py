# -*- coding: utf-8 -*-
"""**"末期不对"是不是守恒口径的问题** —— 验 `xingzhuang.md:147` 那条自述:

    「面积守恒是二维代理, 而锥面族的'绕轴体积'比平顶弓形少; 球越满差得越多」

本探针把每一帧画出来的那个二维区域拿出来, **分别按两种口径算它占多少**:
  * 二维面积占比  = ∫(H−B)dx / (πRi²)      ← app 的"体积"模型(它保证 = 有效落沙比)
  * 绕轴回转体积占比 = ∫2π|x|(H−B)dx / (4/3·πRi³)   ← 真三维下的沙量
两者之差 = 形状造成的"看着没塞满"。

跑法: python tools/_probe_fill_deficit.py [周期]
"""
import math
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
N = 400

with tempfile.TemporaryDirectory(prefix="fill-deficit-") as home:
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
            w.set_duration(PERIOD); w.reset(); w._rebuild_height_table(); w.toggle()
            Ri = w._R_inner
            prof = w._mound_profile
            dt = 1.0 / 60.0

            def measure():
                apex = w._mound_apex()
                if apex <= 0.0:
                    return 0.0, 0.0, 0.0
                # ⚠️ 只积 x ≥ 0 一侧 —— `∫_{-Ri}^{Ri} 2π|x|h dx` 会把同一个壳算**两遍**
                #    (正对照: 整球弦必须给 1.0000; 第一版给 1.9994, 就是被这条抓住的)。
                dxs, step = [], 2.0 * Ri / (N - 1)
                a = v = 0.0
                for i in range(N // 2, N):
                    dx = -Ri + i * step                           # dx ≥ 0 那一半
                    y, _free, _th = prof.column(dx, apex)          # 高度(离球内底)
                    f, _r, _o = prof.geometry_at(dx)               # 该列的球内底
                    h = y - f                                      # 该列实际沙厚
                    if h <= 0.0:
                        continue
                    a += h * step                                  # 面积: 两侧各半, 这里只算一半
                    v += 2.0 * math.pi * dx * h * step             # 壳: 每侧各出现一次, 不翻倍
                return 2.0 * a / (math.pi * Ri * Ri), v / ((4.0 / 3.0) * math.pi * Ri ** 3), apex

            print("")
            print("  周期 %.0fs   球内半径 %.1f" % (PERIOD, Ri))
            print("  %8s %10s %12s %12s %10s" % ("elapsed", "有效落沙f", "二维面积占比", "绕轴体积占比", "差(体积亏)"))
            for k in range(0, 101):
                t = PERIOD * k / 100.0
                while w.elapsed < t - 1e-9:
                    now[0] += dt; w.tick(dt)
                f = w._effective_fallen() if hasattr(w, "_effective_fallen") else float("nan")
                af, vf, apex = measure()
                if t >= PERIOD * 0.80 or k % 10 == 0:
                    print("  %8.2f %10.4f %12.4f %12.4f %10.4f"
                          % (w.elapsed, f, af, vf, af - vf))
            self.stop()

    Probe().run()
