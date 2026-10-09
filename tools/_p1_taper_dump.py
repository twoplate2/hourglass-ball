# -*- coding: utf-8 -*-
"""【1号】颈部几何数值: in_pts / out_pts / 镜像下喇叭口 / 球内顶。
跑法: python tools/_p1_taper_dump.py [周期]
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0

with tempfile.TemporaryDirectory(prefix="p1taper-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
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
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD); w._rebuild_height_table()
            tp = w._taper
            print("")
            print("  _neck_y=%.1f  y_bot=%.1f  t_in=%.2f  t_out=%.2f"
                  % (w._neck_y, tp["y_bot"], tp["t_in"], tp["t_out"]))
            print("  outlet(直筒下端)=%.1f  inlet(直筒上端)=%.1f  直筒高=%.1f"
                  % (2 * w._neck_y - tp["y_bot"], tp["y_bot"], 2 * tp["y_bot"] - 2 * w._neck_y))
            print("  in_pts[0]=(%.2f, %.2f)  in_pts[-1]=(%.2f, %.2f)  n=%d"
                  % (tp["in_pts"][0][0], tp["in_pts"][0][1],
                     tp["in_pts"][-1][0], tp["in_pts"][-1][1], len(tp["in_pts"])))
            print("  out_pts[0]=(%.2f, %.2f)  out_pts[-1]=(%.2f, %.2f)"
                  % (tp["out_pts"][0][0], tp["out_pts"][0][1],
                     tp["out_pts"][-1][0], tp["out_pts"][-1][1]))
            print("  球内顶=_lower_sand_top=%.1f  球内底=_lower_sand_bot=%.1f  R_inner=%.1f"
                  % (w._lower_sand_top, w._lower_sand_bot, w._R_inner))
            print("  上球 _upper_sand_bot=%.1f  _upper_sand_top?=%.1f"
                  % (w._upper_sand_bot, w._upper_sand_bot + 2 * w._R_inner))
            print("  下喇叭口: 镜射轴 y=%.1f ⇒ 其内腔顶=outlet=%.1f, 底=%.1f"
                  % (w._neck_y, 2 * w._neck_y - tp["y_bot"], 2 * w._neck_y - tp["in_pts"][0][1]))
            print("  中轴 x=0 处: 球内顶=%.1f, 到 outlet 还有 %.1f px 的空腔"
                  % (w._lower_sand_top, 2 * w._neck_y - tp["y_bot"] - w._lower_sand_top))
            print("  " + "-" * 60)
            # **下喇叭口(镜射) 与 下球内壁 的衔接**: 决定"球∪喇叭口"有没有折角
            # ⚠️ in_pts 是**上**喇叭口; 下喇叭口 = 关于 neck_y 镜射。
            #    镜射点 (x, y) → (x, 2·neck_y − y); 要跟**下球**的内壁比, 不是上球。
            mir = 2.0 * w._neck_y
            (x0, y0), (x1, y1) = tp["in_pts"][0], tp["in_pts"][1]
            my0, my1 = mir - y0, mir - y1
            d = my0 - w._lower_y_c
            roof0 = w._lower_y_c + (w._R_inner ** 2 - x0 * x0) ** 0.5
            roof1 = w._lower_y_c + (w._R_inner ** 2 - x1 * x1) ** 0.5
            print("  镜射后 (%.2f, %.2f)   下球内壁在该 x 处 y = %.2f   差 = %.2f px"
                  % (x0, my0, roof0, my0 - roof0))
            print("  镜射喇叭口斜率(外→内) = %.4f   下球内壁斜率 = %.4f   差 = %.4f"
                  % ((my1 - my0) / (x1 - x0), (roof1 - roof0) / (x1 - x0),
                     (my1 - my0) / (x1 - x0) - (roof1 - roof0) / (x1 - x0)))
            print("  " + "-" * 60)
            print("  %8s %8s %10s %10s" % ("y", "内半宽(镜像)", "球内顶半宽", "备注"))
            for y in (w._lower_sand_top - 60, w._lower_sand_top, w._lower_sand_top + 10,
                      2 * w._neck_y - tp["in_pts"][0][1], (w._lower_sand_top +
                       2 * w._neck_y - tp["y_bot"]) / 2, tp["y_bot"] - 20,
                      2 * w._neck_y - tp["y_bot"]):
                hw = w._neck_width_at(2 * w._neck_y - y)
                d = y - w._lower_y_c
                ball = (w._R_inner ** 2 - d * d) ** 0.5 if abs(d) <= w._R_inner else 0.0
                print("  %8.1f %8.2f %10.2f" % (y, hw, ball))
            self.stop()

    Probe().run()
