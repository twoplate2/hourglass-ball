# -*- coding: utf-8 -*-
"""**颈部沙柱下沿"跟沙走" vs "钉在玻璃线上"** —— 改前/改后取帧(给用户判)。

读 `HG_NECK_JOIN`(0=现在的行为 / 1=下沿跟着沙走, 见 `main.py` 的 `_neck_sand_side`)。
两棵树各跑一次, 同一批时刻、同一沙色、同一尺寸。

跑法:
    HG_NECK_JOIN=0 python tools/_probe_seam_ab.py seamA
    HG_NECK_JOIN=1 python tools/_probe_seam_ab.py seamB
出图: benchmark_logs/_vid/seam/seamA_*.png
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

TAG = sys.argv[1] if len(sys.argv) > 1 else "seam"
PERIOD = 50.0
TIMES = (5.0, 25.0, 46.0, 48.0, 49.0, 49.8)

with tempfile.TemporaryDirectory(prefix="seam-ab-") as home:
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
            out = ROOT / "benchmark_logs" / "_vid" / "seam"
            out.mkdir(parents=True, exist_ok=True)   # ⚠️ 建的是 **out 自己**, 不是 out.parent
                                                     # (第一版漏了 ⇒ export_to_png 静默不写)
            print("")
            print("  NECK_JOIN =", m.NECK_JOIN, " 周期 %.0fs  平板口径 R_inner=%.1f"
                  % (PERIOD, w._R_inner))
            dt = 1.0 / 60.0
            for t in TIMES:
                while w.elapsed < t - 1e-9:
                    now[0] += dt
                    w.tick(dt)
                w.running = False
                w.redraw()
                p = out / ("%s_%05.1f.png" % (TAG, t))
                w.export_to_png(str(p))
                side = w._neck_sand_side()
                print("  t=%5.1f  沙堆顶=%7.1f  出口=%7.1f  沙柱下沿=%7.1f  缝=%6.1f"
                      % (t, w.get_mound_top_y(),
                         2 * w._neck_y - w._taper["y_bot"],
                         side[-1][1] if side else float("nan"),
                         (2 * w._neck_y - w._taper["y_bot"]) -
                         (side[-1][1] if side else float("nan"))))
                w.running = True
            print("  图 ->", out.relative_to(ROOT))
            self.stop()

    Probe().run()
