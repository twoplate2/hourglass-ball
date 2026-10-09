# -*- coding: utf-8 -*-
"""【1号 · 可复现性猎手】出口那条硬线: 逐行"沙子断成几段"的实测。

复现 K1 的前提数字(段数 1→2→4→9 …)与出口以下/以上的横断面。
跑法: python tools/_p1_segments.py [周期] [进度] [窗口宽 高] [density]
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
PROGRESS = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
WW = int(sys.argv[3]) if len(sys.argv) > 3 else 1904
HH = int(sys.argv[4]) if len(sys.argv) > 4 else 2890
DENS = sys.argv[5] if len(sys.argv) > 5 else "2.75"
os.environ.setdefault("KIVY_METRICS_DENSITY", DENS)
os.environ["HG_FLOW_RENDERER"] = "texture"

import tempfile                                  # noqa: E402

with tempfile.TemporaryDirectory(prefix="p1seg-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    ABL = os.environ.get("HG_P1_ABLATE", "")
    if "stream" in ABL:
        m.HourglassWidget._draw_stream = lambda self: None
    if "mound" in ABL:
        m.HourglassWidget._draw_mound_shape = lambda self, h: None
    if "contact" in ABL:
        m.HourglassWidget._draw_contact_grains = lambda self: None
    if "neck" in ABL:
        # 只一个调用点(main.py:6432) ⇒ 整条实心沙柱(quads + 两片矩形)一起摘掉
        m.HourglassWidget._neck_sand_side = lambda self: []
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])
    import random as _rnd
    _rnd.seed(20261009)          # ⚠️ 不 seed 的话两次跑的粒子位置不同, 对照就是假的

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (WW, HH)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD)
            w.reset(); w._rebuild_height_table()
            w.toggle()
            for _ in range(int(PERIOD * PROGRESS * 60)):
                now[0] += 1.0 / 60.0
                w.tick(1.0 / 60.0)
            w.running = False
            w.redraw()
            out = ROOT / "benchmark_logs" / "_ab_splash" / (
                "p1seg_%s.png" % (os.environ.get("HG_P1_TAG", "base")))
            out.parent.mkdir(parents=True, exist_ok=True)
            w.export_to_png(str(out))
            # —— 事后逐件消融: 只动顶点/尺寸, 不重画 ——
            POST = os.environ.get("HG_P1_POST", "")
            if POST:
                if "q" in POST:
                    w._neck_quads.clear(); w._neck_quads.flush()
                if "r" in POST:
                    w._neck_solid_rect.size = (0, 0)
                    w._neck_fade_rect.size = (0, 0)
                if "g" in POST:
                    w._contact_grains.clear(); w._contact_grains.flush()
                if "n" in POST:                       # 摘掉颈部颗粒批层(Android 那 65 条)
                    ctx = getattr(w, "_neck_context", None)
                    if ctx is not None:
                        w.canvas.remove(ctx)
                        print("  [post] 已摘掉 _neck_context")
                    else:
                        print("  [post] 没有 _neck_context(不是批处理路径)")
                if "G" in POST:                       # 摘掉桌面那 961 条死层
                    grp = getattr(w, "_neck_grain_group", None)
                    if grp is not None and grp in w.canvas.children:
                        w.canvas.remove(grp)
                        print("  [post] 已摘掉 _neck_grain_group(%d 条)"
                              % (len(grp.children) * 2))
                if "t" in POST:      # 把 neck_flow 的 sand_tail 挪到出口以下 ⇒ 只该动矩形
                    ctx = w._sand_flow_contexts[1] if len(w._sand_flow_contexts) > 1 else None
                    if ctx is not None:
                        _o = 2 * w._neck_y - w._taper["y_bot"]
                        _ti = w._taper["t_in"]
                        ctx["sand_tail"] = (_o - 50.0, 1.0, _ti)
                        ctx._tail_key = (_o - 50.0, 1.0, _ti)
                        print("  [post] sand_tail 改到 y=%.1f (出口 y=%.1f)" % (_o - 50, _o))
                w.export_to_png(str(out.with_name("p1post_%s.png" % POST)))

            from PIL import Image
            import numpy as np
            im = Image.open(out).convert("RGBA")
            bg = Image.new("RGB", im.size, (253, 246, 227)); bg.paste(im, (0, 0), im)
            a = np.asarray(bg, np.int16)
            H, W = a.shape[:2]
            r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
            sand = (r > 185) & (b < 180) & ((r - b) > 45)
            cx = int(round(w._cx - w.x))          # 🔴 图像列 = 窗口 x − widget.x
            outlet = 2 * w._neck_y - w._taper["y_bot"]
            inlet = w._taper["y_bot"]
            t_in = w._taper["t_in"]
            mound = w.get_mound_top_y()
            # 🔴 图像行 = (widget.y + widget.height) − 窗口 y。
            #    不减去 w.y 的话整幅图会**整体偏移 w.y 行**(这里是 148.5),
            #    于是"出口以上"的量尺其实落在上球里 —— `_probe_below_outlet.py`
            #    就是这个口径, 它的所有行号都偏低 w.y。
            _top = w.y + w.height
            to_img = lambda y: int(round(_top - y))            # Kivy y(上) -> 图像行(下)
            print("")
            print("  窗口 %dx%d  density=%s  R_inner=%.1f" % (WW, HH, DENS, w._R_inner))
            print("  图 %dx%d   cx=%d  inlet y=%.1f(row %d)  outlet y=%.1f(row %d)  t_in=%.1f"
                  % (W, H, cx, inlet, to_img(inlet), outlet, to_img(outlet), t_in))
            print("  沙堆顶 y=%.1f(row %d)  空档 = %d px" % (mound, to_img(mound),
                                                             to_img(mound) - to_img(outlet)))
            qv = w._neck_quads._v
            ys_q = [qv[k] for k in range(1, len(qv), 4) if qv[k] or qv[k - 1]]
            import numpy as _np
            pys = _np.asarray(w.py[:w.pn]) if w.pn else _np.zeros(1)
            below = int((pys < outlet).sum())
            print("  粒子 pn=%d  y 范围 %.1f..%.1f  出口以下 %d 颗  首 5 个 y=%s"
                  % (w.pn, pys.min(), pys.max(), below,
                     [round(float(v), 1) for v in pys[:5]]))
            print("  _neck_quads 顶点 y 范围 = %.1f .. %.1f  (n 非零顶点 %d)"
                  % (min(ys_q), max(ys_q), len(ys_q)))
            print("  solid_rect pos=%s size=%s a=%.2f | fade_rect pos=%s size=%s | quads=%s"
                  % (tuple(round(v, 1) for v in w._neck_solid_rect.pos),
                     tuple(round(v, 1) for v in w._neck_solid_rect.size),
                     w._neck_solid_color.a,
                     tuple(round(v, 1) for v in w._neck_fade_rect.pos),
                     tuple(round(v, 1) for v in w._neck_fade_rect.size),
                     len(w._neck_quads)))
            lo, hi = cx - int(t_in) - 4, cx + int(t_in) + 5
            print("")
            print("  逐行: 窗口 = 管内宽 %d px (x %d..%d)" % (hi - lo, lo, hi))
            print("  %-7s %-7s %-5s %-6s %-8s %s" % ("row", "kivy_y", "segs", "maxrun", "沙/宽", "runs"))
            r0, r1 = to_img(outlet) - 70, to_img(mound)
            for yy in range(max(0, r0), min(H, r1)):
                row = sand[yy, lo:hi]
                if not row.any():
                    continue
                pad = np.concatenate(([0], row.view(np.int8), [0]))
                idx = np.flatnonzero(np.diff(pad))
                runs = idx[1::2] - idx[0::2]
                tag = ""
                if abs(yy - to_img(outlet)) <= 1:
                    tag = "   <<< outlet(出口)"
                print("  %-7d %-7.1f %-5d %-6d %-8s %s%s"
                      % (yy, H - yy, len(runs), runs.max(),
                          "%.0f%%" % (100.0 * row.mean()),
                          ",".join(map(str, runs)), tag))
            # 出口以上的横断面(实心?) —— 取 outlet 上方 5/15/30 px 三行
            print("")
            print("  outlet 以上 5/15/30 px 三行的沙色像素数(窗口 %d px):" % (hi - lo))
            for d in (5, 15, 30):
                yy = to_img(outlet + d)
                row = sand[yy, lo:hi]
                print("    +%-3dpx row %d : %d / %d" % (d, yy, row.sum(), row.size))
            self.stop()

    Probe().run()
