# -*- coding: utf-8 -*-
"""冻结帧里**每条叶子指令的包围盒** —— 找出"出口以下 56px 宽的那条带"到底是哪条指令画的。"""
import os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"
with tempfile.TemporaryDirectory(prefix="a1bb-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics import Rectangle, Quad, Line, Ellipse, Mesh
    m.HourglassWidget.load_config = lambda *_: {"duration": 600.0}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])
    import random as _rnd; _rnd.seed(20261009)

    def bbox(ins):
        try:
            if isinstance(ins, Rectangle):
                x, y = ins.pos; w, h = ins.size
                return (x, y, x + w, y + h)
            if isinstance(ins, Ellipse):
                x, y = ins.pos; w, h = ins.size
                return (x, y, x + w, y + h)
            if isinstance(ins, Quad):
                p = ins.points
                return (min(p[0::2]), min(p[1::2]), max(p[0::2]), max(p[1::2]))
            if isinstance(ins, Line):
                p = list(ins.points)
                if len(p) < 2: return None
                return (min(p[0::2]), min(p[1::2]), max(p[0::2]), max(p[1::2]))
            if isinstance(ins, Mesh):
                v = list(ins.vertices)
                xs = v[0::4]; ys = v[1::4]
                if not xs: return None
                return (min(xs), min(ys), max(xs), max(ys))
        except Exception:
            return None
        return None

    def walk(node, path, out, depth=0):
        ch = getattr(node, "children", None)
        name = type(node).__name__
        if ch is None:
            bb = bbox(node)
            out.append((path, name, bb))
            return
        for c in ch:
            walk(c, path + "/" + name, out, depth + 1)

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (1904, 2890)
            Clock.schedule_once(self.go, 0.6)
        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(600.0); w.reset(); w._rebuild_height_table()
            w.toggle()
            for _ in range(120):
                now[0] += 1.0/60.0; w.tick(1.0/60.0)
            w.running = False; w.redraw()
            outlet = 2*w._neck_y - w._taper["y_bot"]
            print("  cx=%.1f outlet=%.1f t_in=%.2f  x∈[902,957](成像坐标)+22.5 偏移 => 画布坐标约 [%.0f,%.0f]"
                  % (w._cx, outlet, w._taper["t_in"], 902+0, 957+0))
            for cname in ("canvas", "canvas.before", "canvas.after"):
                cvs = getattr(w, cname, None)
                if cvs is None: continue
                out = []
                walk(cvs, cname, out)
                print("")
                print("  == %s: %d 条叶子 ==" % (cname, len(out)))
                for path, name, bb in out:
                    if bb is None: continue
                    x0, y0, x1, y1 = bb
                    if x1 < 700 or x0 > 1150:          # 只关心颈部一带
                        continue
                    if y1 < 1150 or y0 > 1450:          # 只关心出口上下 220px
                        continue
                    print("    %-42s %-9s x[%8.2f..%8.2f] y[%8.2f..%8.2f]" % (path, name, x0, x1, y0, y1))
            self.stop()
    Probe().run()
