# -*- coding: utf-8 -*-
"""**坐标系标定**: 把候选几何线画进画面, 用"哪一行变色"直接读出图像行 ↔ 窗口 y 的映射。

判据是**实测的**(不猜 `top - y`): 三条标记线各用纯色 2px 横线, 导出后按颜色找行。

用法: python tools/_p1_probe_rows.py [周期] [进度] [宽] [高]
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
PROG = float(sys.argv[2]) if len(sys.argv) > 2 else 0.98
W, H = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (1904, 2890)
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75" if W > 1000 else "1.0")
os.environ["HG_FLOW_RENDERER"] = "texture"

with tempfile.TemporaryDirectory(prefix="p1-rows-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics import Color, Rectangle
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
            Window.system_size = (W, H)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD); w.reset(); w._rebuild_height_table()
            w.toggle()
            while w.elapsed < PERIOD * PROG - 1e-9 and w.running:
                now[0] += 1.0 / 60.0
                w.tick(1.0 / 60.0)
            w.running = False
            w.redraw()
            outlet = 2 * w._neck_y - w._taper["y_bot"]
            marks = [("outlet", outlet, (1, 0, 0)),
                     ("lower_sand_top", w._lower_sand_top, (0, 1, 0)),
                     ("mound_top", w.get_mound_top_y(), (0, 0, 1)),
                     ("outlet_minus_60", outlet - 60.0, (1, 0, 1))]
            print("  w.pos=%s w.size=%s  neck_y=%.2f  outlet=%.2f" % (w.pos, w.size, w._neck_y, outlet))
            for name, y, rgb in marks:
                # ⚠️ 必须**加进 `w.canvas`** —— 裸写 `Color(...)`/`Rectangle(...)`
                #    (不在 `with canvas:` 里) 不会被任何画布画出来, 而且静默(第一版就是这么空转的)。
                w.canvas.add(Color(*rgb, 1))
                w.canvas.add(Rectangle(pos=(0, y - 1.0), size=(w.width, 2.0)))
            out = ROOT / "benchmark_logs" / "_vid" / "seam"
            out.mkdir(parents=True, exist_ok=True)
            p = out / "rows_marked.png"
            w.export_to_png(str(p))
            from PIL import Image
            import numpy as np
            im = np.asarray(Image.open(str(p)).convert("RGB")).astype(np.int16)
            print("  image shape", im.shape)
            for name, y, rgb in marks:
                tgt = np.array(rgb, dtype=np.int16) * 255
                hit = np.all(np.abs(im - tgt) < 40, axis=2)
                rows = np.nonzero(hit.any(axis=1))[0]
                cols = np.nonzero(hit.any(axis=0))[0]
                if rows.size:
                    print("  %-16s 窗口 y=%8.2f  ->  图像行 %5d..%5d  列 %5d..%5d"
                          % (name, y, rows.min(), rows.max(), cols.min(), cols.max()))
                else:
                    print("  %-16s 窗口 y=%8.2f  ->  (画面里找不到, 被裁掉?)" % (name, y))
            self.stop()

    Probe().run()
