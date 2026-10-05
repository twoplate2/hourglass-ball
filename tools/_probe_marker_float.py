# -*- coding: utf-8 -*-
"""表层滑动标记: 上球那几颗是不是**浮在沙面之上**? (r27-1号 报告, 机制自评"中把握")

他的现象: 档6 / 暂停态, 上球沙面上方 4-10px 处有 8-10 段米白断续短条(117~277px);
档4(出厂默认)/档1 都是 0px。他猜的机制: 画沙面的可见上界不含 `+ rough`, 而标记含。

**读代码对不上**: `main.py:3646-3647` 画沙面**就是** `level − drop + rough`, 标记
(`main.py:3566`)用同一个 `_upper_rough_at`, 且 `_ri = round((dx+Ri)/step)` 正是
`dx = −Ri + step·i` 的逆 ⇒ 两侧同一个面, rough 同加同减, **不可能因 rough 而浮**。
⇒ 机制存疑。但**现象可能是真的** ⇒ 自己复现, 不靠读代码判他错。

### 两处真实的**不对称**(读代码查出来的, 这才是该量东西)
① **弦宽**: 画沙面 `hc_shape = √(Ri²−(Ri−upper_height)²)`(用 `upper_height`);
   标记判定 `hc_mark = √(Ri²−(Ri−lvl)²)`, 而 `lvl = _upper_level_for(upper_height) ≥
   upper_height` ⇒ **标记的弦更宽**。弦外那段**没有 carve** ⇒ 标记可以落在沙体之外。
② **幅度 vs 内移**: `_uamp = UPPER_ROUGH_FRAC · 2Ri` **随部件缩放**, 而
   `SURFACE_MARKER_INSET = 4.0` 是**死像素** ⇒ 设备(部件大 ~2.7x)上两者比例与桌面不同。

### 量具(先标定)
受控 A/B: **同一帧、同一档位**, `SURFACE_MARKERS_*` 置 0 = 关, 逐像素差分 ⇒ 差出来的
就是标记本身。深度按**生产公式**算, 但**分弦内/弦外两段报** —— 弦外的"沙面"无意义。
⚠️ 采样时刻**绝不能取整**: `frac = (elapsed/0.9) % 1`, elapsed=9.0 时正好整除,
   相位全落网格上、一批 `fade≈0.2` ⇒ 浓度差只剩 ~5 级被阈值滤掉 ⇒ 假"几乎没有标记"。

跑法: python tools/_probe_marker_float.py
"""
import math
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DURATION = 20
STEADY = 9.137          # ⚠️ 见上: 不能取整
LEVELS = ("6", "4", "1")
THRESH = 4


def main():
    with tempfile.TemporaryDirectory(prefix="markerfloat-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from PIL import Image
        import numpy as np

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": DURATION}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        def shot():
            ww, hh = map(int, Window.size)
            px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
            return np.asarray(Image.frombytes("RGBA", (ww, hh), px).transpose(
                Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"), np.int16)

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
                w.running = True
                dt = 1.0 / 60.0
                while w.elapsed < STEADY:
                    now[0] += dt
                    w.tick(dt)
                w.running = False           # 暂停 ⇒ 标记冻结
                self.w = w
                self.HH = int(Window.size[1])
                self.li = 0
                Clock.schedule_once(self.next_level, 0.30)

            def next_level(self, _dt):
                if self.li >= len(LEVELS):
                    Clock.schedule_once(lambda d: self.stop(), 0.2)
                    return
                self.lvl = LEVELS[self.li]
                self.w.set_rough_level(self.lvl)
                m.SURFACE_MARKERS_UP, m.SURFACE_MARKERS_LOW = 8, 12
                self.w.redraw()
                Clock.schedule_once(self.shot_on, 0.30)

            def shot_on(self, _dt):
                self.A = shot()
                # 右这里是"标记开"那一帧 —— `_surface_marker_n` 就是本帧真发出的颗数
                self.n_mark = getattr(self.w, "_surface_marker_n", -1)
                m.SURFACE_MARKERS_UP = m.SURFACE_MARKERS_LOW = 0
                self.w.redraw()
                Clock.schedule_once(self.shot_off, 0.30)

            def shot_off(self, _dt):
                m.SURFACE_MARKERS_UP, m.SURFACE_MARKERS_LOW = 8, 12
                self.analyze(self.lvl, self.A, shot())
                self.li += 1
                Clock.schedule_once(self.next_level, 0.20)

            def row(self, y_kivy):
                return self.HH - 1 - int(self.w.to_window(0, y_kivy)[1])

            def analyze(self, lvl, A, B):
                w = self.w
                Ri = w._R_inner
                d = np.abs(A - B).max(axis=2)
                print("")
                print("  ===== 沙面起伏 档 %s =====" % lvl)
                print("    差分像素数 @>2/>4/>8/>16 : %d / %d / %d / %d"
                      % ((d > 2).sum(), (d > 4).sum(), (d > 8).sum(), (d > 16).sum()))
                ys, xs = np.nonzero(d > THRESH)
                if len(ys) == 0:
                    print("    **差分为空** ⇒ 这把尺子此刻量不到标记, 下面的数不作判据")
                    return
                uh = w._upper_sand_height_px()
                p = min(1.0, w.elapsed / w.duration)
                dd, bb = w._upper_funnel_params(p, uh)
                lvlk = w._upper_sand_bot + w._upper_level_for(uh)
                hc_shape = math.sqrt(max(0.0, Ri * Ri - (Ri - min(2 * Ri, uh)) ** 2))
                hc_mark = math.sqrt(max(0.0, Ri * Ri - (Ri - min(2 * Ri, lvlk)) ** 2))
                print("    本帧实际发出标记 %d 颗 (上球池 8 + 下球池 12)" % self.n_mark)
                print("    uh=%.2f  _upper_level_for(uh)=%.2f  2Ri=%.2f"
                      % (uh, w._upper_level_for(uh), 2 * Ri))
                print("    弦宽: 画沙面 %.2f | 标记判定 %.2f  (差 %+.2f px)"
                      % (hc_shape, hc_mark, hc_mark - hc_shape))
                cxw = w.to_window(w._cx, 0)[0]
                n = m.MOUND_SHAPE_NODES - 1
                step = 2.0 * Ri / n
                ytop = w.to_window(0, w._upper_sand_bot + 2 * Ri)[1]
                ybot = w.to_window(0, w._neck_y)[1]
                inside, outside, other = [], 0, 0
                for i in range(len(ys)):
                    rw = self.HH - 1 - int(ys[i])          # 窗口 y
                    dxl = xs[i] - cxw
                    if not (ybot <= rw <= ytop):
                        other += 1
                        continue
                    if abs(dxl) > hc_shape:
                        outside += 1                        # 弦外: 没有 carve, 不算深度
                        continue
                    _i = max(0, min(n, int(round((dxl + Ri) / step))))
                    ysurf = (lvlk - w._upper_surface_drop(dxl, dd, bb)
                             + w._upper_rough_at(_i, uh))
                    inside.append(ysurf - rw)               # Kivy y 向上 ⇒ 正 = 在沙面**下**
                print("    像素归属: 弦内 %d | **弦外(沙体之外) %d** | 不在上球区间 %d"
                      % (len(inside), outside, other))
                if inside:
                    a = np.array(inside)
                    print("    弦内像素的垂直深度: min %+.2f  中位 %+.2f  max %+.2f"
                          % (a.min(), np.median(a), a.max()))
                    print("    ⇒ **浮在沙面之上(深度<0) 的像素: %d 个 (%.1f%%)**"
                          % (int((a < 0).sum()), 100.0 * (a < 0).mean()))

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
