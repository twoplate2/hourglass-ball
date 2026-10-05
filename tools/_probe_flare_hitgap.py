# -*- coding: utf-8 -*-
"""下喇叭口肩部的**点击死缝**: 画出来的玻璃有多宽 vs 实际能点多宽。

r28-2号 2026-10-06 设备实测: 下半喇叭口肩部有一条带子点不动(50s ≈6px 高, 60000s ≈33px),
中心列始终可点, 左右对称。

### ⚠️ 这里有过两处, 第一版探针只看见了一处
命中判定(`on_touch_down`)是 **三选一**:
    ① 落在上球圆内  ② 落在下球圆内  ③ `_neck_half_width(y)` 非 None 且 |dx| <= 它 + ow
而第一版探针量"③ 与画出来的玻璃"之差时, **遇到 ③ 返回 None 就 `continue` 跳过** ——
**恰好跳过了真正有问题的那一段**(下喇叭口的下半段, 它已经在 `y_low` 以下, 只剩②)。
⇒ 量出来"死缝 0.0", 与设备上"那一点仍然点不动"**直接矛盾**。**判据自己把被测对象排除了。**

### 这一版改成与 `on_touch_down` **同一套判据**
逐行二分出"实际能点到的最远 |dx|"(①②③ 三选一), 与"画出来的玻璃半宽"比。
负对照: 上喇叭口那一段应当恒为 0。

跑法: python tools/_probe_flare_hitgap.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DURS = (50, 600, 60000)


def main():
    with tempfile.TemporaryDirectory(prefix="flaregap-") as home:
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
        m.HourglassWidget.load_config = lambda *_: {"duration": 50}
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
                print("")
                print("  %-8s | %-10s %-10s | %-9s %-16s | %s"
                      % ("周期", "直筒半宽", "喇叭口半宽", "最宽死缝", "死缝纵向(行数)", "上喇叭口对照"))
                print("  " + "-" * 84)
                for d in DURS:
                    w.set_duration(d)
                    tp = w._taper
                    pts = tp["out_pts"]
                    t_out = tp["t_out"]
                    mir = 2.0 * w._neck_y
                    y_knee = tp["y_bot"]
                    y_out = max(p[1] for p in pts)
                    y_lo_draw = mir - y_out
                    y_hi_draw = y_out

                    def drawn_half(y):
                        yf = max(y, mir - y)
                        if yf <= y_knee:
                            return t_out
                        for (w0, a), (w1, b) in zip(pts, pts[1:]):
                            lo, hi = (a, b) if a <= b else (b, a)
                            if lo - 1e-9 <= yf <= hi + 1e-9:
                                if abs(b - a) < 1e-9:
                                    return max(w0, w1)
                                return w0 + (w1 - w0) * (yf - a) / (b - a)
                        return t_out

                    def hittable(y, dx):
                        """与 `on_touch_down` 逐字同构的三选一。"""
                        if dx * dx + (y - w._upper_y_c) ** 2 <= w._R ** 2:
                            return True
                        if dx * dx + (y - w._lower_y_c) ** 2 <= w._R ** 2:
                            return True
                        half = w._neck_half_width(y)
                        return half is not None and dx <= half + w._ow

                    worst, lo_g, hi_g, up_worst = 0.0, None, None, 0.0
                    for y in range(int(y_lo_draw) - 6, int(y_hi_draw) + 7):
                        dh = drawn_half(y)
                        if dh <= 0:
                            continue
                        # 二分: 从 0 到 dh+40 找最大可点 dx(判据单调, 三选一各自单调)
                        a, b = 0.0, dh + 40.0
                        if hittable(y, 0.0):
                            for _ in range(24):
                                mid = 0.5 * (a + b)
                                if hittable(y, mid):
                                    a = mid
                                else:
                                    b = mid
                        reach = a
                        gap = 2.0 * max(0.0, dh - reach)      # 左右合计
                        if y < w._neck_y:
                            if gap > worst:
                                worst = gap
                            if gap > 0.5:
                                if lo_g is None:
                                    lo_g = y
                                hi_g = y
                        else:
                            up_worst = max(up_worst, gap)
                    print("  %-8s | %-10.1f %-10.1f | %-9.1f %-16s | %.1f"
                          % ("%ds" % d, t_out, max(p[0] for p in pts), worst,
                             ("y %d~%d (%d行)" % (lo_g, hi_g, hi_g - lo_g + 1))
                             if lo_g is not None else "无",
                             up_worst))
                print("")
                print("  单位: widget 局部像素。死缝 = 该行左右两侧合计点不到的玻璃宽度。")
                print("  负对照: 上喇叭口那列若为 0.0 ⇒ 尺子在没事的地方确实量不到东西。")
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
