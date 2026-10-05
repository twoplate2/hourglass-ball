# -*- coding: utf-8 -*-
"""沙体矩形上界(`level`, **不含 rough**) vs 画出来的沙面线(`level − drop + rough`)。
—— 这条**差**就是"亮带画到玻璃上"的成因(r27-1号 报的沙面上方淡色小帽)。

若某节点的 `rough(i) > drop(dx)` ⇒ 该处沙面线**高于矩形上沿**
⇒ 矩形在那儿没有沙, 而**亮带仍沿沙面线画** ⇒ `sand_light @ 0.55` 合成到**玻璃**上
= 淡色小帽。深谷侧则相反(沙面线低于 level, carve 切进沙里) ⇒ 橙色锯齿朝下。

⚠️ 本探针**不下"好不好看"的判断**, 只回答"这条差存不存在、在哪些 x 上、多大"。
⚠️ 同时打印**设备/桌面两个尺度**下的结果 —— 幅度 `_uamp ∝ 2Ri` 随尺寸缩放,
   而 `SAND_SURFACE_BAND = 3.0` 是死像素 ⇒ 两边的可见性可能不同。

跑法: python tools/_probe_rect_vs_surface.py
"""
import math
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="rectsurf-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        from kivy.core.window import Window

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 20}
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
                w.set_duration(20)
                w.running = True
                dt = 1.0 / 60.0
                while w.elapsed < 9.137:
                    now[0] += dt
                    w.tick(dt)
                w.running = False
                Ri = w._R_inner
                uh = w._upper_sand_height_px()
                p = min(1.0, w.elapsed / w.duration)
                d, b = w._upper_funnel_params(p, uh)
                lvl = w._upper_level_for(uh)
                n = m.MOUND_SHAPE_NODES - 1
                print("")
                print("  Ri=%.2f  upper_height=%.2f  level=%.2f  漏斗(depth=%.2f, 半宽=%.2f)"
                      % (Ri, uh, lvl, d, b))
                print("  band_w=%.2f  SAND_SURFACE_ALPHA=%.2f  _uamp=%.3f"
                      % (min(m.SAND_SURFACE_BAND, uh), m.SAND_SURFACE_ALPHA,
                         m.UPPER_ROUGH_FRAC * 2.0 * Ri))
                print("")
                print("  --- 修复后: 矩形顶 = level + `_upper_rough_crest()`, 必须处处 >= 沙面线 ---")
                for lv in ("6", "5", "4", "3", "2", "1"):
                    w.set_rough_level(lv)
                    over = []
                    mx = -1e9
                    for i in range(n + 1):
                        dx = -Ri + (2.0 * Ri / n) * i
                        hc = math.sqrt(max(0.0, Ri * Ri - (Ri - min(2.0 * Ri, lvl)) ** 2))
                        if abs(dx) > hc:
                            continue
                        surf = lvl - w._upper_surface_drop(dx, d, b) + w._upper_rough_at(i, uh)
                        delta = surf - lvl          # >0 = 沙面线**高于**沙体矩形上沿
                        mx = max(mx, delta)
                        if delta > 0.5:
                            over.append((dx, delta))
                    tot = sum(1 for i in range(n + 1)
                              if abs(-Ri + (2.0 * Ri / n) * i)
                              <= math.sqrt(max(0.0, Ri * Ri - (Ri - min(2.0 * Ri, lvl)) ** 2)))
                    crest = w._upper_rough_crest(uh)
                    over2 = []
                    for i in range(n + 1):
                        dx = -Ri + (2.0 * Ri / n) * i
                        hc = math.sqrt(max(0.0, Ri * Ri - (Ri - min(2.0 * Ri, lvl)) ** 2))
                        if abs(dx) > hc:
                            continue
                        surf = lvl - w._upper_surface_drop(dx, d, b) + w._upper_rough_at(i, uh)
                        if surf - (lvl + crest) > 1e-9:
                            over2.append((dx, surf - (lvl + crest)))
                    print("  档 %s: 弦内节点 %3d; 修复前越界 %3d 个(峰 %+.2fpx) | "
                          "crest=%+.2f ⇒ **修复后越界 %d 个**"
                          % (lv, tot, len(over), mx, crest, len(over2)))
                    if over:
                        segs = []
                        cur = [over[0]]
                        for a in over[1:]:
                            if a[0] - cur[-1][0] <= 2.0 * Ri / n * 1.5:
                                cur.append(a)
                            else:
                                segs.append(cur); cur = [a]
                        segs.append(cur)
                        print("        成段 %d 处, 每段 (x范围, 峰值超出):" % len(segs))
                        for s in segs[:6]:
                            print("          x %+7.1f~%+7.1f  峰 %+.2f px  (%d 节点)"
                                  % (s[0][0], s[-1][0], max(v for _x, v in s), len(s)))
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
