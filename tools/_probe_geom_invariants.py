# -*- coding: utf-8 -*-
"""沙漏几何在**一大批窗口尺寸**下的不变量 —— 截图之外的互补一刀。

起因: 今天两条真缺陷都是"只在特定几何下才暴露"(亮带露到矩形外; 底栏在 density 480/560 挤爆)。
而沙漏几何**全部从 widget 尺寸派生**(`R = min(宽约束, 高约束)` + 由 R 反推 ball_h),
所以"尺寸"是一条从没被算式级扫过的轴。两位专家在用**截图**扫, 这里用**数值**扫同一条轴。

守的不变量(每条都先说清"违反了长什么样"):
  ① 两个球都是**正圆**且**半径相同** —— 否则一球大一小球, 或球被拉扁
  ② 两球 + 颈部**完全落在 widget 内**(含描边 `ow`) —— 否则被容器裁掉
  ③ 颈部在**两球正中**; `y_bot > neck_y`(护栏, 已有断言)
  ④ `t_out > t_in > 0`(管壁有厚度、孔是正的)
  ⑤ 过渡段起点半宽 `w_out >= 肩台半宽`(否则"扁平肩台+硬折角"重现)
  ⑥ 不存在 NaN / 负半径 / 零除

跑法: python tools/_probe_geom_invariants.py
"""
import math
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIZES = [(320, 560), (384, 631), (400, 800), (720, 1280), (1080, 1920),
         (1080, 2400), (1200, 2560), (1600, 2560), (2560, 1600), (2400, 1080),
         (1080, 1080), (800, 1280), (1440, 3200), (600, 2000), (2000, 600)]


def main():
    with tempfile.TemporaryDirectory(prefix="geominv-") as home:
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
                print("  %-12s %-7s %-7s | %s" % ("尺寸", "R", "ow", "违反的不变量"))
                print("  " + "-" * 92)
                nbad = 0
                for (W, H) in SIZES:
                    w.size = (W, H)
                    w._rebuild_height_table()
                    bad = []
                    R, Ri, ow = w._R, w._R_inner, w._ow
                    cx = w._cx
                    uyc, lyc = w._upper_y_c, w._lower_y_c
                    tp = w._taper
                    # ① 正圆 + 半径相同(代码里两球共用 R ⇒ 这里查"是否都在 widget 内"即可反映)
                    # ② 完全落在 widget 内
                    # ⚠️ **`_upper_y_c`/`_cx` 等是"窗口坐标", 不是 widget 局部坐标**
                    #    (源码: `cx = self.x + w / 2.0`, `neck_y = self.y + h / 2.0`)。
                    #    第一版拿 widget 的**高度** H 去比 ⇒ 差了一个 `w.y`, 15 个尺寸里
                    #    误报 11 个"上溢出"。**这是今天第四次"判据自己写错"**。
                    x0, y0 = w.x, w.y
                    if cx - R - ow < x0 - 0.5:
                        bad.append("左溢出 %.1f" % (cx - R - ow - x0))
                    if cx + R + ow > x0 + W + 0.5:
                        bad.append("右溢出 %.1f" % (cx + R + ow - x0 - W))
                    if uyc + R + ow > y0 + H + 0.5:
                        bad.append("上溢出 %.1f" % (uyc + R + ow - y0 - H))
                    if lyc - R - ow < y0 - 0.5:
                        bad.append("下溢出 %.1f" % (lyc - R - ow - y0))
                    # ③ 颈部在两球正中
                    mid = 0.5 * (uyc + lyc)
                    if abs(mid - w._neck_y) > 1.0:
                        bad.append("颈不在两球正中(偏 %.1f)" % (mid - w._neck_y))
                    if not (tp["y_bot"] > w._neck_y):
                        bad.append("y_bot<=neck_y")
                    # ④ 管壁厚度
                    if not (tp["t_out"] > tp["t_in"] > 0):
                        bad.append("t_out=%.2f t_in=%.2f" % (tp["t_out"], tp["t_in"]))
                    # ⑤ 过渡起点半宽 >= 肩台半宽
                    shoulder = math.sqrt(max(0.0, R * R - Ri * Ri))
                    w_out = max(p[0] for p in tp["out_pts"])
                    if w_out < shoulder - 0.5:
                        bad.append("w_out=%.1f < 肩台 %.1f" % (w_out, shoulder))
                    # ⑥ NaN / 非正
                    for nm, v in (("R", R), ("Ri", Ri), ("ow", ow), ("cx", cx),
                                  ("neck_w", w.neck_w), ("y_bot", tp["y_bot"])):
                        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                            bad.append("%s 非有限(%s)" % (nm, v))
                    if R <= 0 or Ri <= 0 or w.neck_w <= 0:
                        bad.append("非正尺寸")
                    if bad:
                        nbad += 1
                    print("  %-12s %-7.1f %-7.1f | %s"
                          % ("%dx%d" % (W, H), R, ow, "; ".join(bad) if bad else "ok"))
                print("")
                print("  %d/%d 个尺寸有违反" % (nbad, len(SIZES)))
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
