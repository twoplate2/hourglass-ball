# -*- coding: utf-8 -*-
"""【1号】画布节点普查带 bbox: 谁在画 (x=932, y=1291) 那一条?

跑法: python tools/_p1_canvas_bbox.py [周期] [进度]
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
PROGRESS = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

import tempfile                                  # noqa: E402

TARGETS = [(932, 1291), (932, 1360), (952, 1300), (900, 1250)]

with tempfile.TemporaryDirectory(prefix="p1bbox-") as home:
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
            Window.system_size = (1904, 2890)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD)
            w.reset(); w._rebuild_height_table()
            w.toggle()
            for _ in range(int(PERIOD * PROGRESS * 60)):
                now[0] += 1.0 / 60.0
                w.tick(1.0 / 60.0)
            w.running = False
            w.redraw()

            def bbox(node):
                try:
                    if hasattr(node, "points"):
                        pts = list(node.points)
                        if not pts:
                            return None
                        xs = pts[0::2]; ys = pts[1::2]
                        if max(abs(v) for v in xs) == 0 and max(abs(v) for v in ys) == 0:
                            return None
                        return (min(xs), min(ys), max(xs), max(ys))
                    if hasattr(node, "size") and hasattr(node, "pos"):
                        x, y = node.pos; dw, dh = node.size
                        if dw <= 0 or dh <= 0:
                            return None
                        return (x, y, x + dw, y + dh)
                    if hasattr(node, "vertices"):
                        v = list(node.vertices)
                        if not v:
                            return None
                        # 逐四边形剔掉退化(全 0)的那种 —— 否则 bbox 恒含原点
                        keep = []
                        for q in range(0, len(v), 16):
                            quad = v[q:q + 16]
                            if any(abs(quad[k]) > 1e-9 for k in
                                   (0, 1, 4, 5, 8, 9, 12, 13)):
                                keep.append(quad)
                        if not keep:
                            return None
                        xs = [q[k] for q in keep for k in (0, 4, 8, 12)]
                        ys = [q[k] for q in keep for k in (1, 5, 9, 13)]
                        return (min(xs), min(ys), max(xs), max(ys))
                except Exception:
                    return "ERR"
                return None

            rows = []
            def walk(node, path):
                for ch in getattr(node, "children", ()):
                    bb = bbox(ch)
                    rows.append((path + "/" + type(ch).__name__, bb))
                    walk(ch, path + "/" + type(ch).__name__)
            for i, ch in enumerate(w.canvas.children):
                rows.append(("canvas[%d]:%s" % (i, type(ch).__name__), bbox(ch)))
                walk(ch, "canvas[%d]:%s" % (i, type(ch).__name__))
            print("")
            print("  画布顶层节点 %d 个, 全部后代 %d 个" % (len(w.canvas.children), len(rows)))
            for tx, ty in TARGETS:
                print("")
                print("  === 覆盖点 (x=%d, y=%d) 的节点 ===" % (tx, ty))
                for name, bb in rows:
                    if isinstance(bb, tuple) and bb[0] <= tx <= bb[2] and bb[1] <= ty <= bb[3]:
                        print("    %-60s bbox=%s" % (name, tuple(round(v, 1) for v in bb)))
            self.stop()

    Probe().run()
