# -*- coding: utf-8 -*-
"""飞溅的**高度轴** —— 用户 2026-10-06 追问「除了数量不同, 肯定还有高度也不同」。

用户更早定过的口径: 「沙子飞溅的高度应该也是变化的, **越在中心 越高**」。
本探针量三件事, 全部用**离当地沙面的高度**(px, 绝对)表示:

  ① **按 |dx| 分箱** —— 中轴 vs 边缘, 高度差不差(验"越在中心越高")
  ② **按周期分档** —— 1/5/15/60s 的高度分布(验"不同周期高度不同")
  ③ **出生瞬间 vs 在途** —— 出生点抬多高、活着的时候最高到哪

⚠️ "离沙面的高度"必须用**同一帧**的 `_mound_top_at(x)` 算 —— 沙面自己在涨,
   跨帧比 y 会把"沙面涨了"读成"飞溅落了"。

跑法: python tools/_probe_splash_height.py [周期]     # 不给则扫 1/5/15/60
"""
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PERIODS = (1.0, 5.0, 15.0, 60.0)


def run_one(duration):
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    sys.argv = [sys.argv[0], "_h_probe"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 50}
    m.HourglassWidget.save_config = lambda *_: None

    # 在**生成时**打 `_bg` 标记(和 sigma 探针同一手法: 包一层按长度差打标)
    _orig = m.HourglassWidget._spawn_bg_splashes

    def _marked(self, dt):
        n0 = self._sn
        _orig(self, dt)
        self._s_tag_from(n0, 1)      # 标记位跟着压实走, 见 `_s_tag_from`

    m.HourglassWidget._spawn_bg_splashes = _marked

    acc = {"live": [], "spawn": [], "edge": []}   # live/spawn: (|dx|, h_above)

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            hg = self.hourglass
            hg.duration = duration
            hg._rebuild_height_table()
            hg.toggle()

            def sample(*_a):
                if not hg.running:
                    return
                t = float(hg.elapsed)
                if not (duration * 0.35 <= t <= duration * 0.65):
                    return
                cx = hg._cx
                for s in hg.splashes:
                    dx = abs(s["x"] - cx)
                    h = s["y"] - hg._mound_top_at(s["x"])   # 同一帧的当地沙面
                    acc["live"].append((dx, h, s.get("_tag", 0)))
                acc["edge"].append(hg._mound_edge())

            Clock.schedule_interval(sample, 1.0 / 30.0)
            Clock.schedule_once(lambda dt: self.stop(), duration + 0.5)

    P().run()
    return acc


def q(vals, p):
    if not vals:
        return float("nan")
    v = sorted(vals)
    return v[min(len(v) - 1, int(p * len(v)))]


def main():
    periods = [float(sys.argv[1])] if len(sys.argv) > 1 else list(PERIODS)
    print("")
    print("  === 飞溅离**当地沙面**的高度 (px, 绝对值; Kivy y 向上) ===")
    print("  %-7s %-8s %-9s %-9s %-9s %s"
          % ("周期", "沙堆边缘", "在途 h p50", "h p90", "h max", "在途 n"))
    rows = []
    for d in periods:
        try:
            acc = run_one(d)
        except Exception as exc:
            print("  %-7s 异常 %r" % (d, exc))
            continue
        live = acc["live"]
        if not live:
            print("  %-7s 没采到" % d)
            continue
        hsl = [h for _dx, h, _b in live]
        edge = statistics.median(acc["edge"]) if acc["edge"] else float("nan")
        rows.append((d, edge, live))
        print("  %-7.0f %-8.1f %-9.2f %-9.2f %-9.2f %d"
              % (d, edge, q(hsl, .5), q(hsl, .9), max(hsl), len(live)))

    # ① 越在中心越高? —— 按 |dx|/edge 分 5 箱
    print("")
    print("  === ① 按 |dx| 分箱(归一化到该档沙堆边缘), 看「越在中心越高」 ===")
    for d, edge, live in rows:
        print("  周期 %.0fs  (边缘 %.0fpx):" % (d, edge))
        for b in range(5):
            lo, hi = b / 5.0, (b + 1) / 5.0
            sel = [h for dx, h, _b in live
                   if edge > 0 and lo <= dx / edge < hi]
            if len(sel) < 20:
                print("    %s  n=%-5d (太少)" % ("%.1f~%.1f" % (lo, hi), len(sel)))
                continue
            print("    |dx|/边缘 %s  n=%-5d  h p50=%6.2f  p90=%6.2f  max=%6.2f"
                  % ("%.1f~%.1f" % (lo, hi), len(sel),
                     q(sel, .5), q(sel, .9), max(sel)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
