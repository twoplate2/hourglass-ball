# -*- coding: utf-8 -*-
"""落点横向散布 —— 含**笔画半宽**(专家 xingzhuang2.md §5.4)。

为什么重测: 原来那个"最大 9px"是评审在 5s 档量的、而且只看了**已被删除粒子**的中心
坐标; 专家指出 (a) 它没覆盖不同尺寸/周期, (b) 粒子近底那 30px 才加宽, 命中那一刻
`dist_to_floor=0` 该分支反而不执行 ⇒ 记录的命中范围可能小于**可见**粒子范围。
⇒ 平台半宽 b 必须覆盖 "中心偏移 + 笔画半宽"。

做法(与物理路径无关, 不碰 main.py): 自己驱动 widget, 每 tick 前后快照 (px,py,psz),
检测"本帧跨过碰撞面"的粒子, 记 `|x-cx| + size*0.5`; 逐档统计中位/p95/最大。

跑法: python tools/_xz_impact_spread.py
"""
import os
import random
import statistics
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="xz-impact-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as module
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window

        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": 60}
        module.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])

        results = []
        cases = [(1, 3.0), (5, 12.0), (60, 90.0), (600, 200.0), (36000, 400.0)]
        env_size = os.environ.get("XZ_SIZE")
        sizes = ([tuple(int(v) for v in env_size.split(","))] if env_size
                 else [(400, 800), (720, 1280)])

        class Probe(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1)

            def begin(self, _dt):
                Clock.unschedule(self.hourglass.tick)
                Clock.schedule_once(self.next_size, 0.2)

            def next_size(self, _dt):
                if not sizes:
                    self.report()
                    self.stop()
                    return
                w, h = sizes.pop(0)
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(w / ratio), round(h / ratio))
                Clock.schedule_once(self.run_case, 0.4)

            def run_case(self, _dt):
                if not cases:
                    Clock.schedule_once(self.next_size, 0.2)
                    return
                period, seconds = cases.pop(0)
                wgt = self.hourglass
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                wgt.parent.do_layout()
                wgt.set_duration(period)
                wgt.completion_enabled = False
                if not wgt.running:
                    wgt.toggle()
                random.seed(23)
                wgt.redraw = lambda: None            # 只看物理
                cx = wgt._cx
                hits = []
                steps = int(seconds * 120)
                for _ in range(steps):
                    # ⚠️ 不能"前后快照比 y" —— 命中的粒子当帧就被删除, 数组会塌缩,
                    #    下标对不上粒子(实测这样做一个命中都抓不到)。
                    #    改成量**即将落地**的可见粒子: 距接触面 ≤3px 的(专家 §5.4 原话)。
                    now[0] += 1 / 120
                    wgt.tick(1 / 120)
                    mt = wgt.get_mound_top_y()
                    for i in range(wgt.pn):
                        if 0.0 <= wgt.py[i] - mt <= 3.0:
                            hits.append(abs(wgt.px[i] - cx) + wgt.psz[i] * 0.5)
                if hits:
                    hits.sort()
                    results.append((wgt.width, wgt.height, period, len(hits),
                                    statistics.median(hits),
                                    hits[int(len(hits) * 0.95)],
                                    hits[-1], wgt._mound_plateau_half))
                wgt.redraw = type(wgt).redraw
                wgt.running = False
                Clock.schedule_once(self.run_case, 0.1)

            def report(self):
                print("%-11s %-8s %6s %8s %8s %8s %8s %9s"
                      % ("窗口", "周期", "命中数", "中位", "p95", "最大", "平台半宽", "最大/平台"))
                for w, h, p, n, med, p95, mx, b in results:
                    print("%4dx%-6d %6ss %8d %8.1f %8.1f %8.1f %9.2f %9s"
                          % (w, h, p, n, med, p95, mx, b,
                             "OK" if mx <= b else "**超出**"))
                worst = max((r[6] / r[7] for r in results), default=0)
                print("最大落点 / 平台半宽 = %.2f  (≤1 才说明平台盖得住落点)" % worst)

        Probe().run()


if __name__ == "__main__":
    main()
