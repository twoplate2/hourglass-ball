# -*- coding: utf-8 -*-
"""飞溅颗粒从**落到坡面**到**消失**滑了多远、花了多久 —— 用户 2026-10-06。

用户口径:「沙子的向下流动应该是**流动一会会就不动了**(因为有阻力), 否则全部向下流动,
但是**下面没有堆积**, 这个不合理」。

单帧看不出一颗滑了多远 ⇒ 必须**逐帧跟踪同一颗**。做法: 包一层 `_eject_splash` 给每颗
打 `_tag`, 之后每帧按 tag 记 `x`; 第一次出现 `_rest > 0` 的那一刻 = 着陆点,
消失前最后一次 = 终点。

产出: 滑行距离 |Δx| 与滑行时长 `_rest` 的 p50/p90/max, 以及"停住后还留了多久"。

跑法: python tools/_probe_splash_slide.py [周期] [采样起点秒]
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
FROM = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    sys.argv = [sys.argv[0], "_sps"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None

    counter = [0]
    orig_eject = m.HourglassWidget._eject_splash
    landed = {}          # tag -> (着陆 x, 着陆时刻 perf_counter)
    result = []          # (|Δx|, 滑动时长, 停住后又留了多久)
    last_seen = {}       # tag -> (x, now, _rest, _still)

    def eject(self, x, y_surface, v_impact):
        d = orig_eject(self, x, y_surface, v_impact)
        if d is not None:
            counter[0] += 1
            d["_tag"] = counter[0]
        return d

    m.HourglassWidget._eject_splash = eject
    import time as _t

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            self.hg = self.hourglass
            self.hg.set_duration(PERIOD)
            self.hg._rebuild_height_table()
            self.hg.reset()
            self.hg.toggle()
            Clock.schedule_interval(self.sample, 1.0 / 60.0)
            Clock.schedule_once(lambda dt: self.stop(), min(PERIOD, 30.0))

        def sample(self, _dt):
            hg = self.hg
            now = _t.perf_counter()
            if hg.elapsed < FROM:
                return
            alive = set()
            for s in hg.splashes:
                tag = s.get("_tag")
                if tag is None:
                    continue
                alive.add(tag)
                if s.get("_rest", 0.0) > 0.0 and tag not in landed:
                    landed[tag] = (s["x"], now)
                last_seen[tag] = (s["x"], now, s.get("_rest", 0.0),
                                  s.get("_still", 0.0))
            for tag in list(landed):
                if tag in alive:
                    continue
                x0, t0 = landed.pop(tag)
                x1, t1, rest, still = last_seen.get(tag, (x0, t0, 0.0, 0.0))
                result.append((abs(x1 - x0), rest, still, t1 - t0))

    P().run()
    return result


if __name__ == "__main__":
    import statistics
    res = run()
    res = [r for r in res if r[1] > 0.0]
    print("")
    print("  === 飞溅颗粒: 落坡之后滑了多远 / 多久 (周期 %gs, 采样自 %gs) ===" % (PERIOD, FROM))
    if not res:
        print("  没采到(可能这段时间没有飞溅)"); raise SystemExit
    dist = sorted(r[0] for r in res)
    slide = sorted(r[1] for r in res)
    still = sorted(r[2] for r in res)
    def q(a, p):
        return a[min(len(a) - 1, int(len(a) * p))]
    print("  样本 %d 颗" % len(res))
    print("  滑行距离 |Δx| (px): p50 %6.1f   p90 %6.1f   max %6.1f"
          % (q(dist, .5), q(dist, .9), dist[-1]))
    print("  滑动时长 (s)     : p50 %6.2f   p90 %6.2f   max %6.2f"
          % (q(slide, .5), q(slide, .9), slide[-1]))
    print("  停住后滞留 (s)   : p50 %6.2f   p90 %6.2f   max %6.2f"
          % (q(still, .5), q(still, .9), still[-1]))
    print("  单位是**应用坐标**(桌面 400x800); 设备 1080 宽约乘 2.35。")
    print("")
