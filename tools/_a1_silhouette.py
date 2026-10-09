# -*- coding: utf-8 -*-
"""A1 的**像素级**取证: 出口以下的实画轮廓(逐行沙色游程宽度), 两张:
  (a) 全图(柱+粒子)   (b) 把粒子 pn 归零后的**只有柱子**图 —— 用来隔离"柱子轮廓有没有断口"。

跑法: python tools/_a1_silhouette.py [WW] [HH] [DENS] [PERIOD] [AT]
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WW = int(sys.argv[1]) if len(sys.argv) > 1 else 1904
HH = int(sys.argv[2]) if len(sys.argv) > 2 else 2890
DENS = sys.argv[3] if len(sys.argv) > 3 else "2.75"
PERIOD = float(sys.argv[4]) if len(sys.argv) > 4 else 600.0
AT = float(sys.argv[5]) if len(sys.argv) > 5 else 2.0
os.environ.setdefault("KIVY_METRICS_DENSITY", DENS)
os.environ["HG_FLOW_RENDERER"] = "texture"

with tempfile.TemporaryDirectory(prefix="a1-silh-") as home:
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
    import random as _rnd
    _rnd.seed(20261009)

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (WW, HH)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            if (Window.size[0], Window.size[1]) != (WW, HH):
                print("  !! 窗口没拿到 %dx%d, 实得 %s => 本次不作数"
                      % (WW, HH, tuple(Window.size)))
                self.stop()
                return
            self.root.apply_orientation()
            self.root.do_layout()
            self.root._anchor.do_layout()
            self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD)
            w.reset()
            w._rebuild_height_table()
            w.toggle()
            for _ in range(max(1, int(round(AT * 60)))):
                now[0] += 1.0 / 60.0
                w.tick(1.0 / 60.0)
            w.running = False
            w.redraw()

            out = ROOT / "benchmark_logs" / "_a1"
            out.mkdir(parents=True, exist_ok=True)
            full = out / ("silh_full_p%g.png" % PERIOD)
            w.export_to_png(str(full))
            w.px[:w.pn] += 1.0e5         # 粒子整体挪出画布(不能动 pn, 批渲染器会下标越界)
            w.redraw()
            col = out / ("silh_colonly_p%g.png" % PERIOD)
            w.export_to_png(str(col))

            from PIL import Image
            import numpy as np

            def load(path):
                im = Image.open(path).convert("RGBA")
                bg = Image.new("RGB", im.size, (253, 246, 227))
                bg.paste(im, (0, 0), im)
                a = np.asarray(bg, np.int16)
                r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
                return (r > 185) & (b < 180) & ((r - b) > 45)

            outlet = 2 * w._neck_y - w._taper["y_bot"]
            t_in = w._taper["t_in"]
            print("")
            print("  == %dx%d d=%s 周期 %.0fs t=%.2f  出口 y=%.1f  t_in=%.2f =="
                  % (WW, HH, DENS, PERIOD, w.elapsed, outlet, t_in))
            print("  硬断口若在, 应在 depth=12.66 处单边掉 %.3f*%.2f=%.2fpx => 整宽掉 %.2fpx"
                  % (0.205, t_in, 0.205 * t_in, 2 * 0.205 * t_in))
            for name, path in (("柱+粒子", full), ("只有柱子", col)):
                sand = load(path)
                H, W = sand.shape
                cx = int(round(w._cx))
                prof = {}
                for kv_depth in range(0, 141, 2):
                    yy = int(round(H - (outlet - kv_depth)))
                    if not (0 <= yy < H):
                        continue
                    x0, x1 = max(0, cx - 200), min(W, cx + 201)
                    row = sand[yy, x0:x1]
                    if not row[cx - x0]:
                        prof[kv_depth] = 0
                        continue
                    lo = hi = cx - x0
                    while lo > 0 and row[lo - 1]:
                        lo -= 1
                    while hi < row.size - 1 and row[hi + 1]:
                        hi += 1
                    prof[kv_depth] = hi - lo + 1
                keys = sorted(prof)
                vals = [prof[k] for k in keys]
                sm = [int(np.median(vals[max(0, i - 1):i + 2])) for i in range(len(vals))]
                steps = [(abs(sm[i + 1] - sm[i]), keys[i + 1]) for i in range(len(sm) - 1)]
                s = max(steps) if steps else (0, 0)
                print("  [%s] 逐行宽度(每 2px): 最大相邻落差 = %d px @ depth=%d"
                      % (name, s[0], s[1]))
                line = []
                for i, k in enumerate(keys):
                    line.append("%d:%d" % (k, prof[k]))
                print("      " + " ".join(line))
            self.stop()

    Probe().run()
