# -*- coding: utf-8 -*-
"""`_mound_edge` 二分改写 vs 原 2px 线性扫的**数值对照**(项目规矩: 判据先标定)。

原式: 从 R-1 往里 2px 一步, 返回**第一个** has_sand 为真的 x(2px 量化)。
新式: 先试 x=R-1; 否则在 [0, R-1] 上二分 12 步, 返回最后一个为真的 x。

判据: 两者之差应 **<= 2px**(原式本身就是 2px 量化), 且新式不返回"无沙"的点。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
sys.argv = [sys.argv[0], "_mee"]
import main as m                                             # noqa: E402
from kivy.clock import Clock                                 # noqa: E402
from kivy.core.window import Window                          # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *_: None
m.HourglassWidget._make_completion_sound = lambda *_: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 50}
m.HourglassWidget.save_config = lambda *_: None
rows = []


class P(m.HourglassApp):
    def on_start(self):
        Window.size = (400, 800)
        Clock.schedule_once(self.go, 1.2)

    def go(self, _dt):
        hg = self.hourglass
        hg.set_duration(50.0)
        hg._rebuild_height_table()
        hg.reset()
        prof = hg._mound_profile
        Ri = hg._R_inner
        for frac in (0.01, 0.05, 0.15, 0.3, 0.5, 0.75, 0.95, 1.0):
            hg.elapsed = 50.0 * frac
            hg._sync_mound_frame()
            apex = hg._mound_apex()
            if prof is None or apex <= 0.0:
                continue
            # 原式
            x = Ri - 1.0
            old = 0.0
            while x > 0.0:
                if prof.has_sand(x, apex):
                    old = x
                    break
                x -= 2.0
            new = hg._mound_edge()
            # 新式返回的点必须真的"有沙"
            ok = (new <= 0.0) or prof.has_sand(new, apex)
            rows.append((frac, old, new, abs(old - new), ok))
        Clock.schedule_once(lambda dt: self.stop(), 0.2)


P().run()
print("")
print("  === _mound_edge: 线性扫 vs 二分 ===")
print("  %-7s %-10s %-10s %-9s %s" % ("frac", "原式", "二分", "|差|", "二分点有沙"))
worst = 0.0
for frac, old, new, d, ok in rows:
    worst = max(worst, d)
    print("  %-7.2f %-10.2f %-10.2f %-9.2f %s" % (frac, old, new, d, "OK" if ok else "**BAD**"))
print("")
print("  最大差 %.2f px (原式量化步长 2px) ⇒ %s" % (worst, "通过" if worst <= 2.0 else "**不通过**"))
print("")
