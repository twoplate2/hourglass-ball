# -*- coding: utf-8 -*-
"""量: 出口 / 球内顶 / 沙柱下沿 —— 并**看实际画出来的**(按行扫沙色段)。

不读几何推理, 只读像素: 下球中部到底有没有一条贯穿的宽沙条。
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
_SZ = [int(x) for x in os.environ.get("FOOT_SIZE", "1904x2890").split("x")]
_P = float(os.environ.get("FOOT_PERIOD", "50"))
os.environ["HG_FLOW_RENDERER"] = "texture"

TAG = (sys.argv[1] if len(sys.argv) > 1 else "cur")
TIMES = [float(a) for a in sys.argv[2:]] or [20.0]
with tempfile.TemporaryDirectory(prefix="footgeom-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": _P}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])

    class P(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = tuple(_SZ)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation()
            self.root.do_layout()
            self.root._anchor.do_layout()
            self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(_P)
            w.reset()
            w._rebuild_height_table()
            w.toggle()
            y_end = 2 * w._neck_y - w._taper["y_bot"]
            out = ROOT / "benchmark_logs" / "_vid" / ("foot_" + TAG)
            out.mkdir(parents=True, exist_ok=True)
            print("")
            print("  TAG=%s NECK_JOIN=%s NECK_FRONT=%s" % (TAG, m.NECK_JOIN, m.NECK_FRONT))
            print("  R_inner=%.2f  t_in=%.2f  outlet=%.2f  ball_inner_top=%.2f"
                  % (w._R_inner, w._taper["t_in"], y_end, w._lower_sand_top))
            print("  ball_inner_bot=%.2f  len(in_pts)=%d  len(quads)=%d"
                  % (w._lower_sand_bot, len(w._taper["in_pts"]), len(w._neck_quads)))
            print("")
            print("     t     堆顶     柱下沿  len(side)  下球各行沙色段(中轴附近)")
            dt = 1.0 / 60.0
            for t in TIMES:
                while w.elapsed < t - 1e-9:
                    now[0] += dt
                    w.tick(dt)
                w.running = False
                w.redraw()
                s = w._neck_sand_side()
                if not s:
                    print("  %5.1f  (no column)" % t)
                    w.running = True
                    continue
                p = out / ("f_%05.1f.png" % t)
                w.export_to_png(str(p))
                from PIL import Image
                im = Image.open(p).convert("RGB")
                W, H = im.size
                px = im.load()
                rows = []
                for y in (int(w._lower_sand_bot) + 60,
                          int((w._lower_sand_bot + w._lower_sand_top) / 2),
                          int(w._lower_sand_top) - 40,
                          int(y_end) - 30, int(y_end) + 30):
                    yy = H - 1 - y          # PNG 是 y 向下
                    if not (0 <= yy < H):
                        continue
                    bg = px[4, yy]
                    run, best = 0, 0
                    for x in range(W // 2 - 150, W // 2 + 150):
                        c = px[x, yy]
                        if sum(abs(a - b) for a, b in zip(c, bg)) > 24:
                            run += 1
                            best = max(best, run)
                        else:
                            run = 0
                    rows.append("y=%4d:%3dpx" % (y, best))
                print("  %5.1f  %7.1f  %7.1f   %2d      %s"
                      % (t, w.get_mound_top_y(), s[-1][1], len(s), "  ".join(rows)))
                w.running = True
            print("  图 ->", out.relative_to(ROOT))
            self.stop()

    P().run()
