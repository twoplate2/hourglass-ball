# -*- coding: utf-8 -*-
"""**出口以下到底"空"到什么程度** —— 用户 2026-10-09 截图:「红线以上必然实心, 以下必然空心」。

代码事实: `_neck_sand_side()` 的沙柱轮廓到 `outlet`(= 2·neck_y − taper['y_bot'], 直筒**下端**)
为止; 出口以下是**下喇叭口**, 只画粒子、不画实心沙。所以"上实下虚"是**设计**, 不是 bug。

本探针量的是**幅度**: 从出口往下逐行的"沙色覆盖率"(该行沙色像素 / 该行流宽),
和出口以上做对照 —— 这决定改进该"补一根实心柱"还是"把粒子加密就够"。

跑法: python tools/_probe_below_outlet.py [周期] [进度0..1]
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

import tempfile                                  # noqa: E402

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
PROGRESS = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5

with tempfile.TemporaryDirectory(prefix="below-outlet-") as home:
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
            Window.system_size = (1904, 2890)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD)
            w.reset(); w._rebuild_height_table()
            w.toggle()
            target = PERIOD * PROGRESS
            steps = int(target * 60)
            for _ in range(steps):
                now[0] += 1.0 / 60.0
                w.tick(1.0 / 60.0)
            w.running = False
            w.redraw()
            out = ROOT / "benchmark_logs" / "_ab_splash" / ("below_outlet_p%g.png" % PERIOD)
            out.parent.mkdir(parents=True, exist_ok=True)
            w.export_to_png(str(out))

            from PIL import Image
            import numpy as np
            im = Image.open(out).convert("RGBA")
            bg = Image.new("RGB", im.size, (253, 246, 227)); bg.paste(im, (0, 0), im)
            a = np.asarray(bg, np.int16)
            H, W = a.shape[:2]
            r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
            sand = (r > 185) & (b < 180) & ((r - b) > 45)
            cx_px = int(w._cx)
            outlet = 2 * w._neck_y - w._taper["y_bot"]          # Kivy y(向上)
            inlet = w._taper["y_bot"]
            t_in = w._taper["t_in"]
            mound = w.get_mound_top_y()
            to_img = lambda y: int(round(H - y))                # Kivy y → 图像行

            def core_width(yy):
                """该行 **以中轴为中心的连续沙色段** 有多宽(遇到第一个非沙色就停)"""
                lo = hi = cx_px
                w0 = max(0, cx_px - 120); w1 = min(W, cx_px + 121)
                while lo > w0 and sand[yy, lo - 1]:
                    lo -= 1
                while hi < w1 - 1 and sand[yy, hi + 1]:
                    hi += 1
                return (hi - lo + 1) if sand[yy, cx_px] else 0

            def band(y0_kv, y1_kv):
                """两行 Kivy y 之间: 逐行覆盖率(沙色像素 / 该行流宽 2·t_in)"""
                r0, r1 = to_img(y1_kv), to_img(y0_kv)
                cov = []
                for yy in range(max(0, r0), min(H, r1)):
                    x0, x1 = cx_px - int(t_in) - 2, cx_px + int(t_in) + 3
                    row = sand[yy, max(0, x0):min(W, x1)]
                    cov.append(row.mean() if row.size else 0.0)
                return cov

            print("")
            print("  周期 %.0fs  进度 %.0f%%  R_inner=%.0f  直筒 内宽 %.1fpx"
                  % (PERIOD, PROGRESS * 100, w._R_inner, 2 * t_in))
            print("  inlet(直筒上端) y=%.0f   outlet(直筒下端) y=%.0f  沙堆顶 y=%.0f"
                  % (inlet, outlet, mound))
            print("  出口以下的空档高度 = %.0f px (%.1f 倍孔径)" %
                  (outlet - mound, (outlet - mound) / max(1e-9, 2 * t_in)))
            print("")
            print("  %-26s %10s %10s %10s" % ("带", "覆盖率中位", "覆盖<20%的行", "行数"))
            for name, y0, y1 in (("直筒段(出口以上)", outlet, inlet),
                                 ("出口→沙堆: 上 1/3", mound + 2 * (outlet - mound) / 3, outlet),
                                 ("出口→沙堆: 中 1/3", mound + (outlet - mound) / 3, mound + 2 * (outlet - mound) / 3),
                                 ("出口→沙堆: 下 1/3", mound, mound + (outlet - mound) / 3)):
                cov = band(y0, y1)
                if not cov:
                    continue
                import statistics as st
                med = st.median(cov)
                low = sum(1 for c in cov if c < 0.20)
                r0, r1 = to_img(y1), to_img(y0)
                cores = [core_width(yy) for yy in range(max(0, r0), min(H, r1))]
                import statistics as st2
                print("  %-26s %9.1f%% %10d %10d   密集核心宽 %.0f px = 孔径的 %.0f%%"
                      % (name, 100 * med, low, len(cov), st2.median(cores),
                         100.0 * st2.median(cores) / max(1e-9, 2 * t_in)))
            print("")
            print("  ⚠️ 口径: 覆盖率 = 该行沙色像素 / (2·t_in + 5) —— 只看**管内宽**那一条,")
            print("     所以「沙流比孔径宽」的那种行不会被算高。")
            print("     图 -> %s" % out.relative_to(ROOT))
            self.stop()

    Probe().run()
