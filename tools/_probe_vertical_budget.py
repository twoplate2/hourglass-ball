# -*- coding: utf-8 -*-
"""**纵向预算账本**: 屏幕上每一行各吃掉多少 px, 以及"把它砍掉 X 能换回多大的球"(2026-10-07)。

## 为什么需要它

用户连着提了两条:"倒计时文字能不能往上挪些"、"所有按钮高度再降 5% 行不行"。
两条都只有一个判据 —— **省下来的纵向, 能换回多大的 R**。

而这件事**在平板和桌面上结论相反**:
    R = min(R_by_w, R_by_h),  R_by_w = w/2 − 0.06w,  R_by_h = (h − tube_h − 2·v_pad)/4
    宽度受限 ⟺ w/h < 0.5301
- **平板 1904×2890** ⇒ w/h = 0.659 > 0.5301 ⇒ **高度受限** ⇒ 每省 4px 纵向 = **R +1px**(球径 +2px) ✓
- **桌面预览 400×800**(w/h=0.5)⇒ **宽度受限** ⇒ 省纵向 **一点用都没有**, 只会把空白变大

⇒ **这条只能在平板口径上量**。本探针默认就按平板尺寸建窗口, 并**断言读回来的尺寸
就是请求的**(QA_RULES §七 第 6 问: "设置 → run() → 量"的探针必须回读断言 ——
`_probe_popup_height.py` v1 就栽在这, 表里写 1080x1080 实跑 1000x600)。

跑法: python tools/_probe_vertical_budget.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# 平板口径: 窗口 = 物理 1904x3040 减去系统栏 150px; density 由那一百五十像素反推(见下)。
TABLET_W, TABLET_H = 1904, 2890
TABLET_DENSITY = 2.75          # 1904x3040 的 13" 板; 150px 系统栏 ÷ 2.75 = 54.5dp(状态+导航) ✓
os.environ.setdefault("KIVY_METRICS_DENSITY", str(TABLET_DENSITY))
os.environ["HG_FLOW_RENDERER"] = "texture"

from kivy.clock import Clock                     # noqa: E402
from kivy.core.window import Window              # noqa: E402
from kivy.metrics import dp, Metrics              # noqa: E402
import main as m                                 # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *a, **kw: None
m.HourglassWidget._make_completion_sound = lambda *a, **kw: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 50.0}
m.HourglassWidget.save_config = lambda *_: None


def _geom(w, h, *, label_dp=24.0, colors_dp=46.0, bottom_dp=50.0,
          spacing_dp=2.0, pad_dp=2.0, side_frac=0.06, vpad_frac=0.002):
    """按 `_rebuild_height_table` 的**同一套算式**推 R(纯算术, 不建窗口)。

    canvas 各行的 px 消耗 = BoxLayout 从上到下的实际高度:
        根 padding 上 + 色块行 + spacing + 倒计时 + spacing + 画布 + spacing + 底栏 + 根 padding 下
    """
    d = TABLET_DENSITY
    canvas_w = w - 2 * dp(pad_dp)
    canvas_h = h - (dp(pad_dp) + dp(colors_dp) + dp(spacing_dp) + dp(label_dp)
                    + dp(spacing_dp) + dp(spacing_dp) + dp(bottom_dp) + dp(pad_dp))
    tube_h = canvas_h * 0.055
    r_by_w = canvas_w / 2.0 - canvas_w * side_frac
    r_by_h = (canvas_h - tube_h - 2 * canvas_h * vpad_frac) / 4.0
    r = min(r_by_w, r_by_h)
    return canvas_w, canvas_h, r_by_w, r_by_h, r


def main():
    print("")
    print("  平板口径: 窗口 %dx%d, density %.2f (dp(1)=%.2fpx)"
          % (TABLET_W, TABLET_H, TABLET_DENSITY, dp(1)))
    print("")

    base = _geom(TABLET_W, TABLET_H)
    print("  == 现状: 每行各吃多少 (px) ==")
    rows = [("根 padding 上", dp(2)), ("色块行", dp(46)), ("spacing", dp(2)),
            ("倒计时", dp(24)), ("spacing", dp(2)), ("spacing", dp(2)),
            ("底栏", dp(50)), ("根 padding 下", dp(2))]
    for name, px in rows:
        print("     %-12s %7.1f px  (%5.2f%%)" % (name, px, 100.0 * px / TABLET_H))
    print("     %-12s %7.1f px" % ("→ 画布高", base[1]))
    print("     %-12s %7.1f px  (球径 = 2R = %.0f, 占画布宽的 %.1f%%)"
          % ("R", base[4], 2 * base[4], 100.0 * 2 * base[4] / base[0]))
    print("     R_by_w %.1f  vs  R_by_h %.1f  ⇒ **%s受限**, 余量 %.1f%%"
          % (base[2], base[3], "高" if base[3] < base[2] else "宽",
             100.0 * abs(base[2] - base[3]) / base[4]))

    def show(title, **kw):
        cw, ch, rbw, rbh, r = _geom(TABLET_W, TABLET_H, **kw)
        dr, dia = r - base[4], 2 * (r - base[4])
        print("     %-34s 画布高 %7.1f  省 %5.1fpx ⇒ R %6.1f (%+5.1f, %+.2f%%)  球径 %+5.1fpx"
              % (title, ch, base[1] - ch, r, dr, 100.0 * dr / base[4], dia))

    print("")
    print("  == 1.236 之后的**剩余**候选能换回多少 (省纵向 ⇒ R) ==")
    show("色块 46dp → 43.7dp (−5%)", colors_dp=43.7)
    show("底栏 50dp → 47.5dp (−5%)", bottom_dp=47.5)
    show("**所有按钮 −5%**(用户提议)", colors_dp=43.7, bottom_dp=47.5)
    show("倒计时再 24dp → 20dp", label_dp=20.0)
    show("v_pad 0.2% → 0", vpad_frac=0.0)
    show("**上面全部**", colors_dp=43.7, bottom_dp=47.5, label_dp=20.0, vpad_frac=0.0)
    print("     ⚠️ 左右边距(side_frac)在这台板上**完全无效** —— R_by_w 还有 42% 余量, 见上。")
    show("旁证: 左右边距 6% → 4.5%", side_frac=0.045)

    # ---- 实建一次窗口, 断言真实布局与上面的算术对得上 ----
    class Probe(m.HourglassApp):
        def on_start(self):
            Window.size = (TABLET_W, TABLET_H)
            Clock.schedule_once(self.check, 1.2)

        def check(self, _dt):
            if (Window.size[0], Window.size[1]) != (TABLET_W, TABLET_H):
                print("\n  !! 窗口尺寸没拿到: 要 (%d,%d) 实得 %s ⇒ 本次算术不作数"
                      % (TABLET_W, TABLET_H, tuple(Window.size)))
                self.stop()
                return
            hg = self.hourglass
            print("")
            print("  == 实建窗口回读 (断言: 与上面算术同一套) ==")
            print("     Metrics.density = %.3f" % Metrics.density)
            print("     画布 %.0f x %.0f   R = %.1f   球径 %.0f"
                  % (hg.width, hg.height, hg._R, 2 * hg._R))
            print("     色块行 %.1f  倒计时 %.1f  底栏 %.1f"
                  % (self.color_btns[0][1].parent.height, self.time_label.height,
                     self.duration_btn.parent.height))
            cw, ch, rbw, rbh, r = _geom(TABLET_W, TABLET_H)
            ok = abs(ch - hg.height) <= 2.0 and abs(r - hg._R) <= 1.0
            print("     算术画布 %.0f vs 实建 %.0f  算术 R %.1f vs 实建 %.1f  ⇒ %s"
                  % (ch, hg.height, r, hg._R, "对得上 ✓" if ok else "**对不上 ⇒ 本探针的账是错的**"))
            self.stop()

    Probe().run()
    return 0


sys.exit(main())
