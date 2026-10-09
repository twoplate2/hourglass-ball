# -*- coding: utf-8 -*-
"""守卫: `_upper_area` 里那份**内联的下陷公式**必须与 `_upper_surface_drop` 逐字同值。

为什么需要它: 那份内联是为了性能(`_upper_solve_level` 的牛顿内核, 每帧 ~390 次节点迭代),
但它只能在注释里写着"与那一份定义逐字相同" —— 改了定义忘了改它,
"面积求解"和"绘制"用的就不是一条曲线 ⇒ **体积守恒破**(用户 2026-10-05 点名要的那条),
而且**不报错、不崩**, 只是沙量悄悄对不上。本项目最怕的静默失效。

⚠️ **第一版这个探针是废的**(留档): 它把内联那份**又手抄了一遍**放进探针里,
于是两份手抄本互相比 —— **改真正的内联代码它完全不响**。负对照当场抓出来(`if _cone and False:`
照样报"逐位一致")。⇒ 现在**直接调真的 `_upper_area`**, 参照值用真的 `_upper_surface_drop`
+ **widget 自己缓存的列几何**算出来(几何只抄一次, 而且抄错了两边一样错, 不影响本判据)。

判据: 同一组 `(level, d, b)` 上, 两条路算出的面积必须相等(容差 1e-9 相对)。
标定:
  · 已知对 = 当前代码 ⇒ 全过;
  · 已知错 = 把 `_upper_area` 里 `if _cone:` 改成 `if _cone and False:` ⇒ 必须翻红(见文末)。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["KIVY_ARGS"] = ""
os.environ["KIVY_NO_ARGS"] = "1"
os.environ["KIVY_NO_FILELOG"] = "1"

with tempfile.TemporaryDirectory(prefix="funnel-inline-") as _home:
    os.environ["KIVY_HOME"] = _home
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget.load_config = lambda *_: {"duration": 50.0}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (1904, 2890)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation()
            self.root.do_layout()
            self.root._anchor.do_layout()
            self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(50.0)
            w.reset()
            w._rebuild_height_table()

            bad = total = 0
            # 形状开关是 `HG_FUNNEL_SHAPE` 加的; 没这个常量时只跑一种(现状=抛物线)。
            _shapes = ("cone", "parabola") if hasattr(m, "UPPER_FUNNEL_SHAPE") else ("parabola",)
            for shape in _shapes:
                if hasattr(m, "UPPER_FUNNEL_SHAPE"):
                    m.UPPER_FUNNEL_SHAPE = shape
                for d in (0.0, 25.0, 144.0, 420.0):
                    for b in (0.0, 137.5, 566.0):
                        for lv in (0.18, 0.45, 0.83, 0.99):
                            level = lv * 2.0 * w._R_inner
                            area_real, _ = w._upper_area(level, d, b)
                            area_ref, _ = self.reference(w, level, d, b)
                            total += 1
                            if abs(area_real - area_ref) > 1e-9 * max(1.0, abs(area_ref)):
                                bad += 1
                                if bad <= 6:
                                    print("  !! shape=%s lv=%.2f d=%.0f b=%.1f  "
                                          "真=%.9f 参照=%.9f" % (shape, lv, d, b,
                                                              area_real, area_ref))
            print("  比了 %d 组, 不一致 %d 组" % (total, bad))
            print("==> 内联与定义", "分叉了 —— 守恒会破" if bad else "逐位一致")
            self.stop()
            sys.exit(1 if bad else 0)

        def reference(self, w, level, d, b):
            """用**真的** `_upper_surface_drop` + widget 缓存的列几何重算一遍面积。"""
            Ri = w._R_inner
            n = m.MOUND_SHAPE_NODES - 1
            w._upper_area(level, d, b)          # 先跑一次把 `_upper_cols` 填上
            rough = w._upper_rough_now()
            _q = min(1.0, max(0.0, level / (2.0 * Ri))) if Ri > 0 else 0.0
            env = (m._smoothstep(0.0, 0.015, _q) * (1.0 - m._smoothstep(0.97, 1.0, _q))
                   if Ri > 0 else 0.0)
            area = 0.0
            for i in range(n + 1):
                dx, floor, roof, weight = w._upper_cols[i]
                y = level - w._upper_surface_drop(dx, d, b)
                if rough and i < len(rough):
                    y += rough[i] * env * w._upper_wall_weights[i]
                if y <= floor:
                    continue
                area += ((roof - floor) if y >= roof else (y - floor)) * weight
            return area, 0.0

    Probe().run()
