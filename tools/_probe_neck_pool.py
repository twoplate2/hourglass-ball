# -*- coding: utf-8 -*-
"""`_neck_quads` 池子(25)够不够 `_neck_sand_side()` 用? —— 数出来, 不推理。

疑点(读代码得到的):
  · 注满分支  side = [pts 中 y>fill_y 的] + [1]        → 最多 25+1 = 26 点 → 需 25 quad
  · 排空分支  side = [1] + [pts 中 y<fill_y 的] + [1]  → 最多 1+25+1 = 27 点 → 需 26 quad
  而 `for i, quad in enumerate(self._neck_quads)` 只走 25 个 ⇒ **多的那段被静默丢弃**
  (`quad.points = [0]*8`), 表现为颈部沙柱**缺最后一段**。
  但 f=1 时 `fill_y = y_end + (y_top-y_end)*1.0` 是否**严格等于** y_top 是浮点问题,
  `pts[0]` 到底进不进来算不出来 ⇒ 必须实测。

本探针逐帧记 len(side), 覆写整个周期(注满 + 稳态 + 排空), 报最大值与是否越池。
负对照: 必须能看到 ≥26 出现, 否则说明我没跑到那个状态(而不是"没问题")。

跑法: python tools/_probe_neck_pool.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DURATION = 3


def main():
    with tempfile.TemporaryDirectory(prefix="neckpool-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": DURATION}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w.set_duration(DURATION)
                pool = len(w._neck_quads)
                npts = len(w._taper["in_pts"])
                print("")
                print("  池子 %d 个 quad; in_pts %d 点 ⇒ 注满需 %d, 排空需 %d"
                      % (pool, npts, npts + 1 - 1, npts + 2 - 1))
                print("")
                print("  阶段        帧数    len(side) 最大   最小   超池(>%d点) 次数   注满态?" % (pool + 1))
                print("  " + "-" * 66)

                if not w.running:
                    w.toggle()
                w._done_at = None
                dt = 1.0 / 60.0
                phase = {"fill": [[], []], "steady": [[], []]}
                marks = []
                # 覆盖: 注满 / 稳态 / 排空(越过 done 之后再跑一段)
                total = int((DURATION + 1.5) / dt)
                for i in range(total):
                    now[0] += dt
                    w.tick(dt)
                    side = w._neck_sand_side()
                    k = ("fill" if w._done_at is None and w.elapsed < DURATION
                         else ("drain" if w._done_at is not None else "fill"))
                    phase.setdefault(k, [[], []])
                    phase[k][0].append(len(side))
                    phase[k][1].append(w.elapsed)
                    # ⚠️ 判据修正: N 点只需 N-1 段。第一版把 len(side)>=26 当超池,
                    #    而 26 点正好是 25 段 = 池子容量 ⇒ 174 个假警报。
                    if len(side) - 1 > pool:
                        marks.append((w.elapsed, len(side), k))
                for k in ("fill", "drain"):
                    if k not in phase or not phase[k][0]:
                        print("  %-10s **没跑到**(不能据此说没事)" % k)
                        continue
                    v = phase[k][0]
                    over = sum(1 for x in v if x - 1 > pool)
                    print("  %-10s %-6d %-16d %-6d %-16d %s"
                          % (k, len(v), max(v), min(v), over,
                             "是" if k == "fill" else "否"))
                print("")
                if marks:
                    print("  **超池帧 %d 个**, 前 8 个 (elapsed, len(side), 阶段):" % len(marks))
                    for e, n, k in marks[:8]:
                        print("    t=%.3f  len=%d  %s" % (e, n, k))
                else:
                    print("  没有超池帧 —— 池子刚好够(与枚举推导一致)")
                print("")
                print("  抽样(排空段 len(side) 序列前 20):")
                ds = phase.get("drain", [[], []])[0][:20]
                print("   ", ds)
                Clock.schedule_once(lambda d: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
