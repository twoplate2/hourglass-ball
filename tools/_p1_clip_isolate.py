# -*- coding: utf-8 -*-
"""**1号 审查用**: 单进程冻结帧 A/B —— 把 `NECK_JOIN` 的两处影响**分开**:

  ① 延伸段(沙柱多画一段) —— 只可能出现在 **outlet 以下**;
  ② `redraw` 里那条 clip(`if connected and not NECK_JOIN`)**不再清零** [outlet, fade_top]
     那几段的四边形 —— 只可能出现在 **outlet 以上**。

判定: 两帧**冻结同一时刻**(粒子不跑)⇒ 差异**全部**来自这两处。
  · outlet 以上出现差异 ⇒ clip 放开**不是**像素中性的(矩形没盖住 / alpha<1)。
  · outlet 以下出现差异 ⇒ 延伸段**真的画出来了**(而不是被容量丢掉)。

跑法: python tools/_p1_clip_isolate.py [周期] [进度] [宽] [高]
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
PROG = float(sys.argv[2]) if len(sys.argv) > 2 else 0.98
W, H = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (1904, 2890)
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75" if W > 1000 else "1.0")
os.environ["HG_FLOW_RENDERER"] = "texture"

with tempfile.TemporaryDirectory(prefix="p1-clip-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (W, H)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD); w.reset(); w._rebuild_height_table()
            w.toggle()
            target = PERIOD * PROG
            while w.elapsed < target - 1e-9 and w.running:
                now[0] += 1.0 / 60.0
                w.tick(1.0 / 60.0)
            w.running = False                      # 冻结: 之后 redraw 不动任何粒子
            out = ROOT / "benchmark_logs" / "_vid" / "seam"
            out.mkdir(parents=True, exist_ok=True)
            shots = {}
            # ⚠️ 文件名必须带材质标签 —— 第一版不带, 于是 flat 那轮把 grain 那轮的图**覆盖**了
            #    (两张图同名), 量出来的是后跑的那条路。已由 `_p1_probe_rows.py` 实测标定过
            #    `export_as_image` 的映射: 图像行 = (w.y + w.height) − 窗口 y, 列 = 窗口 x − w.x。
            mat = os.environ.get("HG_SAND_MATERIAL", "grain")
            for join in (False, True):
                m.NECK_JOIN = join
                w.redraw()
                p = out / ("clipiso_%s_%s_%s.png" % (mat, "on" if join else "off",
                                                      ("P%g_%g" % (PERIOD, PROG)).replace(".", "p")))
                w.export_to_png(str(p))
                side = w._neck_sand_side()
                shots[join] = (str(p), side)
                print("  [%s] NECK_JOIN=%-5s len(side)=%2d  下沿=%s  出口=%.2f  球内顶=%.2f"
                      % (mat, join, len(side), ("%.2f" % side[-1][1]) if side else "[]",
                         2 * w._neck_y - w._taper["y_bot"], w._lower_sand_top))
            # ---- 逐像素 ----
            from PIL import Image
            import numpy as np
            a = np.asarray(Image.open(shots[False][0]).convert("RGB")).astype(np.int16)
            b = np.asarray(Image.open(shots[True][0]).convert("RGB")).astype(np.int16)
            d = np.abs(a - b).max(axis=2)
            ys, xs = np.nonzero(d > 0)
            outlet_row = (w.y + w.height) - (2 * w._neck_y - w._taper["y_bot"])
            print("\n  两帧尺寸 %s; 出口行(图像坐标, 已标定)=%.1f" % (a.shape, outlet_row))
            print("  差异像素 = %d (%.3f%%)  最大通道差 = %d" % (ys.size, 100.0 * ys.size / d.size, d.max() if ys.size else 0))
            if ys.size:
                print("  差异 bbox: x[%d,%d] y[%d,%d]" % (xs.min(), xs.max(), ys.min(), ys.max()))
                up = int((ys < outlet_row).sum())
                dn = int((ys > outlet_row).sum())
                print("  **出口以上** = %d 像素;**出口以下** = %d 像素" % (up, dn))
                if up:
                    r = ys[ys < outlet_row]
                    print("  ⚠️ 出口以上也有差异: 行范围 y[%d,%d](即 outlet 往上 %.1f px)"
                          % (r.min(), r.max(), outlet_row - r.min()))
            self.stop()

    Probe().run()
