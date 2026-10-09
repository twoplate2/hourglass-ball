# -*- coding: utf-8 -*-
"""量沙柱**下沿的形状**: 前沿说了算时, 最后几个节点是平的还是圆的?"""
import os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"
TAG = sys.argv[1] if len(sys.argv) > 1 else "cur"
PERIOD = float(os.environ.get("P", "600"))
TIMES = [float(a) for a in sys.argv[2:]] or [2.0, 10.0, 60.0]
with tempfile.TemporaryDirectory(prefix="front-") as home:
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

    class P(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (1904, 2890)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD); w.reset(); w._rebuild_height_table(); w.toggle()
            dt = 1.0 / 60.0
            print("")
            print("  TAG=%s 周期%.0fs  柱下沿形状: 最后 3 个节点 (x=半宽, y)" % (TAG, PERIOD))
            print("     t     前沿     堆顶    节点n-3        节点n-2        节点n-1     下沿起伏")
            for t in TIMES:
                while w.elapsed < t - 1e-9:
                    now[0] += dt
                    w.tick(dt)
                w.running = False
                s = w._neck_sand_side()
                if len(s) >= 3:
                    a, b, c = s[-3], s[-2], s[-1]
                    print("  %5.1f  %7.1f  %7.1f  (%6.1f,%7.1f) (%6.1f,%7.1f) (%6.1f,%7.1f)  %6.1f px"
                          % (t, w._falling_front(), w.get_mound_top_y(),
                             a[0], a[1], b[0], b[1], c[0], c[1], abs(b[1] - c[1])))
                w.running = True
            self.stop()
    P().run()
