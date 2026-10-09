# -*- coding: utf-8 -*-
"""**1号 审查用**: 末段密扫 —— 沙柱什么时候"整根消失", 与堆顶到没到管口。

关掉的那条老行为在 `outlet` 就断; 现在多一根到 `_lower_sand_top` 的延伸。
问: 延伸**存在**的时间窗有多长? 它消失之后到计时结束之间, 画面是什么?

跑法: python tools/_p1_tail_scan.py [周期] [环数]
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
PERIODS = [float(v) for v in (sys.argv[1].split(",") if len(sys.argv) > 1 else ["5", "50"])]

with tempfile.TemporaryDirectory(prefix="p1-tail-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": 50.0}
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
            for P in PERIODS:
                w.set_duration(P); w.reset(); w._rebuild_height_table()
                for _ in range(400):
                    _b = len(getattr(w, "_warm_queue", ()))
                    w._warm_batches_step()
                    if len(getattr(w, "_warm_queue", ())) >= _b:
                        break
                w.toggle()
                outlet = 2 * w._neck_y - w._taper["y_bot"]
                print("\n-- 周期 %.0fs: outlet=%.2f 球内顶=%.2f --" % (P, outlet, w._lower_sand_top))
                print("  %8s %9s %9s %6s %9s %9s" % ("t", "堆顶", "柱底", "n", "柱->堆缝", "堆->球内顶"))
                first_gone = None
                t = P * 0.90
                while t <= P + 1e-9:
                    guard = 0
                    while w.elapsed < t - 1e-9 and w.running and guard < 200000:
                        guard += 1
                        now[0] += 1.0 / 120.0
                        w.tick(1.0 / 120.0)
                    if not w.running:
                        break
                    side = w._neck_sand_side()
                    bt = side[-1][1] if side else float("nan")
                    mt = w.get_mound_top_y()
                    if not side and first_gone is None:
                        first_gone = w.elapsed
                    if abs(t * 200 - round(t * 200)) < 1e-9 or t >= P - 1e-9:
                        print("  %8.3f %9.2f %9s %6d %9s %9.2f"
                              % (t, mt, ("%.2f" % bt) if side else "-", len(side),
                                 ("%.2f" % (mt - bt)) if side else "-", w._lower_sand_top - mt))
                    t += P * 0.01
                print("  ⇒ 沙柱消失于 t=%.3f (周期 %.3f 的 %.2f%%)"
                      % (first_gone or float("nan"), P,
                         100.0 * (first_gone or 0) / P))
            self.stop()

    Probe().run()
