# -*- coding: utf-8 -*-
"""独立复算 r20 两位专家的两条结论 —— 子代理的"已验证"标签不能代替证据。

① **1 秒档前 ~56% 下球是空的**（r20-1号，设备逐帧量）——
   桌面直接算 `_mound_height_px()` 在 duration=1 的整轮上是什么形状。纯逻辑, 最便宜。
② **左壁与右壁颜色完全相同**（r20-2号，14 个取样点全同）——
   渲一帧, 在玻璃描边上左右各取一排点比。这条决定"到底有没有光模型"。

跑法: python tools/_verify_r20_claims.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="r20chk-") as home:
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
        m.HourglassWidget.load_config = lambda *_: {"duration": 60}
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
                # ---------- ① 1 秒档: 下沙堆高度 vs 进度 ----------
                print("")
                print("=== ① 下沙堆高度占整轮的比例 (duration=1) ===")
                print("  进度    elapsed   _mound_height_px   _fall_delay   堆高/球高")
                w.set_duration(1)
                w.running = True
                h_ball = 2.0 * w._R_inner
                first = None
                for k in range(0, 21):
                    frac = k / 20.0
                    w.elapsed = frac * 1.0
                    mh = w._mound_height_px()
                    if first is None and mh > 0.5:
                        first = frac
                    print("  %4.0f%%   %.3fs   %8.2f px        %.2fs        %6.1f%%"
                          % (frac * 100, w.elapsed, mh, w._fall_delay, 100.0 * mh / h_ball))
                print("  ⇒ 第一次出现可见沙堆: **%s**"
                      % ("%.0f%%" % (first * 100) if first is not None else "整轮都没有"))
                w.set_duration(60)
                # ---------- ② 左右壁颜色 ----------
                w.running = False
                w.elapsed = 0.0
                w.redraw()
                Clock.schedule_once(self.walls, 0.4)

            def walls(self, _dt):
                import numpy as np
                from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
                from PIL import Image
                w = self.hourglass
                ww, hh = map(int, Window.size)
                px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
                im = np.asarray(Image.frombytes("RGBA", (ww, hh), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"), np.int16)
                cxw = w.to_window(w._cx, 0)[0]
                Ri = w._R_inner
                print("")
                print("=== ② 上球左右壁描边颜色 (逐行, 找非背景的第一个/最后一个像素) ===")
                bg = im[hh - 5, 5]        # 角落 = 背景
                rows = []
                for k in range(1, 9):
                    y = int(w.to_window(w._cx, w._upper_y_c)[1] - k * (Ri * 0.22))
                    r = hh - 1 - y
                    if not (0 <= r < hh):
                        continue
                    line = im[r]
                    diff = np.abs(line - bg.reshape(1, 3)).max(axis=1) > 12
                    idx = np.nonzero(diff)[0]
                    if idx.size < 2:
                        continue
                    lx, rx = int(idx[0]), int(idx[-1])
                    rows.append((y, tuple(int(v) for v in line[lx]),
                                 tuple(int(v) for v in line[rx]), lx, rx))
                print("  窗口y   左壁 RGB          右壁 RGB          相同?")
                same = 0
                for y, l, r, lx, rx in rows:
                    eq = (l == r)
                    same += eq
                    print("  %5d  %-18s %-18s %s" % (y, l, r, "**完全一样**" if eq else "不同"))
                print("  ⇒ %d/%d 行左右壁颜色**完全相同**" % (same, len(rows)))
                print("  ⇒ 若全同: **没有任何方向性明暗** —— 不是「光弱」, 是没有光模型")
                Clock.schedule_once(lambda d: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
