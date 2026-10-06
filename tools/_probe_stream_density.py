# -*- coding: utf-8 -*-
"""下落沙流的**线密度** = 每 100px 长的沙流里有几颗沙 —— 用户 2026-10-06 追问。

用户口径: 「如果我们下落的沙子的速度是固定的, **理论上在大部分飞行时间中沙子的情况
都是近似固定的**」⇒ 若成立, 则飞溅(按"下落沙子的一个比例")也该近似固定。

本探针量三件事, 全部在**运行中段(35%~65%)**、且都换算成"每 100px"以便跨档比:
  ① 在途粒子数 `pn`
  ② 沙流长度 = 颈部出口 → 下球沙堆顶(同一帧)
  ③ 线密度 = pn / 长度 × 100     <- 这才是"看起来有多密"
  ④ 颈口面密度 = pn 里落在颈口附近的比例(用 neck_w 归一) —— 项目 10-03 那条结论管的是它

跑法: python tools/_probe_stream_density.py
"""
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PERIODS = (1.0, 5.0, 15.0, 50.0, 120.0)


def run_one(duration):
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    sys.argv = [sys.argv[0], "_sdp"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

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
            hg.duration = duration
            hg._rebuild_height_table()
            hg.toggle()

            def s(*_a):
                if not hg.running:
                    return
                t = float(hg.elapsed)
                if not (duration * 0.35 <= t <= duration * 0.65):
                    return
                top = hg.get_mound_top_y()          # 沙堆顶(绝对 y)
                bottom = hg._lower_sand_bot
                # 沙流长度: 颈口出口 → 沙堆顶
                exit_y = hg._lower_sand_bot + (hg._R_inner * 2.0)   # 近似: 下球内顶
                exit_y = 2 * hg._neck_y - hg._taper['y_bot']
                length = max(1.0, exit_y - top)
                rows.append((len([1 for _ in range(1)]), int(hg.pn), length,
                             float(hg.neck_w)))

            Clock.schedule_interval(s, 1.0 / 30.0)
            Clock.schedule_once(lambda dt: self.stop(), duration + 0.5)

    P().run()
    if not rows:
        return None
    pn = statistics.median([r[1] for r in rows])
    ln = statistics.median([r[2] for r in rows])
    nw = statistics.median([r[3] for r in rows])
    return {"d": duration, "pn": pn, "len": ln, "neck": nw,
            "per100": pn / ln * 100.0}


if __name__ == "__main__":
    print("")
    print("  === 下落沙流的密度 (运行中段 35%~65%) ===")
    print("  %-8s %-10s %-12s %-10s %s"
          % ("周期", "在途粒子", "沙流长度px", "颈宽px", "每100px几颗"))
    out = []
    for d in PERIODS:
        try:
            r = run_one(d)
        except Exception as exc:
            print("  %-8s 异常 %r" % (d, exc))
            continue
        if not r:
            print("  %-8s 没采到" % d)
            continue
        out.append(r)
        print("  %-8.0f %-10d %-12.0f %-10.1f **%.2f**"
              % (r["d"], r["pn"], r["len"], r["neck"], r["per100"]))
    if len(out) >= 2:
        v = [r["per100"] for r in out]
        print("")
        print("  ⇒ 线密度跨档 **%.2f ~ %.2f 颗/100px, 差 %.1f 倍**"
              % (min(v), max(v), max(v) / max(1e-9, min(v))))
        print("     (用户口径: 「大部分飞行时间中沙子的情况近似固定」⇒ 若成立, 这个数该近似恒定)")
