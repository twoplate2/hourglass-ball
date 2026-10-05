# -*- coding: utf-8 -*-
"""「点沙漏正中间没反应」—— 把那条死区量出来。

r19-2号 2026-10-05 在设备上实测: 空闲态点 (540,1250) 按钮**不变色**(没起跑),
点 (540,1100) 起跑; 运行中点 (540,1250) 也**不暂停**。逐点扫描给出死区
y≈1198~1305(约 **108px**), 并称 x=100 与 x=900 同样无效。

机制(代码事实, `main.py:2980-2989`): `on_touch_down` 要求触点落在
**上下两个球的圆内**, 而两球不相交 ⇒ 两圆之间那一段**没有任何一点**满足条件。

本探针**不点设备**, 直接在 widget 上扫一遍 `on_touch_down` 的命中判定
(用假 touch + 桩掉 `App.get_running_app`, 不改任何状态), 量出:
  ① 死区的高度与位置(与设备实测对照);
  ② 死区之外**不该活的点**有没有被误判为活(横向越界的反例)。

跑法: python tools/_probe_tap_deadzone.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 设备实测的那台是 1080×2400 —— 照这个尺度量才可比
SIZES = [(400, 800), (1080, 2400), (1096, 2214)]


def main():
    with tempfile.TemporaryDirectory(prefix="tapdz-") as home:
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

        class FakeApp:
            """只用来**数** `on_toggle` 调用; 其它属性一律吞掉。

            ⚠️ 第一版只给了 `on_toggle`, 结果 `tick()` 里 `app.update_time(...)`
            直接 AttributeError 把探针打崩 —— 桩要**宽容**, 不然它自己就成了被测对象。
            """
            def __init__(self):
                self.toggles = 0

            def on_toggle(self):
                self.toggles += 1

            def __getattr__(self, _name):
                return lambda *a, **k: None

        fake = FakeApp()
        m.App = types.SimpleNamespace(get_running_app=lambda: fake)

        class FakeTouch:
            def __init__(self, x, y):
                self.x, self.y = x, y
                self.pos = (x, y)

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w.duration = 60
                for sw, sh in SIZES:
                    Window.size = (sw, sh)
                    self.root.apply_orientation()
                    self.root.do_layout()
                    self.root._anchor.do_layout()
                    self.hourglass.parent.do_layout()
                    w._rebuild_height_table()
                    self.sweep(w, sw, sh)
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

            @staticmethod
            def sweep(w, sw, sh):
                P0 = w.to_window(0, 0)
                x0, y0 = P0
                x1, y1 = w.to_window(w.width, w.height)
                xs = range(int(x0) + 2, int(x1) - 2, 4)
                ys = range(int(y0) + 2, int(y1) - 2, 4)

                def hit(px, py):
                    before = fake.toggles
                    w.on_touch_down(FakeTouch(px, py))
                    got = fake.toggles != before
                    fake.toggles = before
                    return got

                # 每一行(y)横向扫, 记"这一行有没有任何一个点能命中"
                dead_rows = []
                live_rows = []
                for py in ys:
                    any_hit = any(hit(px, py) for px in xs)
                    (live_rows if any_hit else dead_rows).append(py)
                print("\n=== widget %dx%d  (窗口 %dx%d)  ===" % (w.width, w.height, sw, sh))
                op = w._taper["out_pts"]
                print("  几何(窗口坐标): 上球心 %.0f 下球心 %.0f R=%.0f | 轮廓 y %.0f~%.0f, 半宽 %.1f~%.1f"
                      % (w.to_window(0, w._upper_y_c)[1], w.to_window(0, w._lower_y_c)[1],
                         w._R, w.to_window(0, min(p[1] for p in op))[1],
                         w.to_window(0, max(p[1] for p in op))[1],
                         min(p[0] for p in op), max(p[0] for p in op)))
                print("  上球下缘 %.0f / 下球上缘 %.0f (窗口 y) —— 两圆之间就是天然死区"
                      % (w.to_window(0, w._upper_y_c - w._R)[1],
                         w.to_window(0, w._lower_y_c + w._R)[1]))
                print("  纵向扫描 %d 行: 有反应 %d 行 / **整行无反应 %d 行**"
                      % (len(list(ys)), len(live_rows), len(dead_rows)))
                if dead_rows:
                    # 合并成连续段
                    segs, s = [], dead_rows[0]
                    for a, b in zip(dead_rows, dead_rows[1:]):
                        if b != a + 4:
                            segs.append((s, a)); s = b
                    segs.append((s, dead_rows[-1]))
                    for a, b in segs:
                        print("    **死带(窗口 y) %d ~ %d → 高 %dpx** —— 屏幕 y 约 %d ~ %d"
                              % (a, b, b - a + 4, sh - b, sh - a))
                # 反例: 修完之后, **不该**变成可点的点
                # 颈部那一行**无条件**要查 —— 只查"还剩几段"的话, 修好之后就查不到了。
                neck_y = int((w.to_window(0, w._upper_y_c - w._R)[1]
                              + w.to_window(0, w._lower_y_c + w._R)[1]) / 2)
                live_x = [px for px in xs if hit(px, neck_y)]
                print("  颈部那一行(窗口 y=%d)可点的 x 范围: %s  (widget 宽 %d)"
                      % (neck_y, ("%d~%d, 共 %d 个采样点" % (min(live_x), max(live_x),
                                                        len(live_x))) if live_x else "**全灭**",
                         int(w.width)))
                print("  反例(不该活的点):")
                checks = [(int(x0) + 3, int((y0 + y1) / 2), "整体最左"),
                          (int(x1) - 3, int((y0 + y1) / 2), "整体最右"),
                          (int(x0) + 3, neck_y, "颈部行·最左"),
                          (int(x1) - 3, neck_y, "颈部行·最右")]
                for px, py, why in checks:
                    print("     %-12s (%d,%d) → %s" % (why, px, py,
                                                    "活了(可疑)" if hit(px, py) else "没反应(对)"))

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
