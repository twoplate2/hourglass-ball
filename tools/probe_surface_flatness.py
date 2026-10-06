# -*- coding: utf-8 -*-
"""量"沙面到底有多不平" —— 下球沙堆 与 上球沙面, 逐列 1 像素扫描。

## 起因
r16-1号 逐列扫下球沙堆, 报「全程恒斜率(约 31°), 偏差只有 ±1~2px, 顶点是两条直线直接相交」。
**但他采样间隔 20px, 而粗糙度是 65 个节点铺在 ~891px 上(=13.7px/节点)**
⇒ 20px 采样会**混叠**, 那个 ±1~2px 不能作为"粗糙度没渲染出来"的证据。

## 本探针
设备尺度窗口(1096x2214, 1 桌面像素 = 1 设备像素), **逐列 1 像素**扫沙面边缘:
  下球: 从下球顶部往下扫, 每列第一个沙色像素 = 堆的轮廓 y(x)
  上球: 从上球顶部往下扫, 每列第一个沙色像素 = 沙面 y(x)
再对轮廓**拟合一条直线**(下球分左右两段), 看**去趋势残差的 RMS / max**。
下球期望幅度 = MOUND_ROUGH_FRAC×2R ≈ 3.56px; 上球期望 = UPPER_ROUGH_FRAC×2R ≈ 1.25px。

⚠️ **内置对照**: 两处都量。若下球残差 ≈3.5 而上球 ≈1.2, 说明探针有分辨力(能区分两个幅度);
   若两个都 ≈0, 说明探针量不出粗糙度(那是探针的锅, 不是 app 的)。

跑法: python tools/probe_surface_flatness.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "flatness"
PIX = (1096, 2214)
SEED = 23
DURATION = 60.0
PROGRESS = 0.5


def main():
    with tempfile.TemporaryDirectory(prefix="flat-") as home:
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

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": int(DURATION)}
        m.HourglassWidget.save_config = lambda *_: None

        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])
        OUT.mkdir(parents=True, exist_ok=True)
        shot = {}

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.resize, 1)

            def resize(self, _dt):
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(PIX[0] / ratio), round(PIX[1] / ratio))
                Clock.schedule_once(self.begin, 0.6)

            def begin(self, _dt):
                self.hg = w = self.hourglass
                w.set_duration(int(DURATION))
                w.completion_enabled = False
                if not w.running:
                    w.toggle()
                w._done_at = None
                import random
                random.seed(SEED)
                t = DURATION * PROGRESS
                guard = 0
                while w.elapsed + 1e-9 < t and w.running:
                    step = min(1.0 / 120.0, t - w.elapsed)
                    now[0] += step
                    w.tick(step)
                    guard += 1
                    if guard > 400000:
                        break
                w.elapsed = t
                w.particles[:] = []
                w.splashes = []
                w.flares[:] = []
                w.dusts[:] = []
                w.redraw()
                Clock.schedule_once(lambda dt: self.grab(), 0.5)

            def grab(self):
                w, h = map(int, Window.size)
                px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
                shot["img"] = Image.frombytes("RGBA", (w, h), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
                self.geo = self.hg.to_window(self.hg._cx, self.hg._lower_y_c)
                self.finish()

            def finish(self):
                analyse(shot["img"], self.hg, self.geo)
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
        # ---- 分析 ----
        return 0


def analyse(img, w, geo):
    """逐列找沙面上边缘。⚠️ 两个坑(第一版都踩了):
       ① 必须要求**连续 N 个沙像素**才算命中 —— 否则会抓到颈口那柱沙/抗锯齿杂点;
       ② 上球沙面是**漏斗(抛物线)**, 拿直线去趋势, 残差里混的是漏斗本身不是粗糙度
          ⇒ 上球用二次去趋势, 下球(直线锥)才用一次。
    """
    import numpy as np
    a = np.asarray(img, dtype=np.int16)
    base = np.array(w.sand_base) * 255.0
    cx = int(round(w.to_window(w._cx, 0)[0]))
    Ri = w._R_inner
    print("图 %s  cx=%d  R_inner=%.2f  沙色=%s" % (img.size, cx, Ri, tuple(int(v) for v in base)))

    d = np.abs(a - base.reshape(1, 1, 3)).max(axis=2)
    is_sand = d < 60

    def scan(cols, y0, y1, run=7):
        out = {}
        for x in cols:
            hit = np.nonzero(is_sand[y0:y1, x])[0]
            if hit.size < run:
                continue
            # 找第一段连续 run 个命中
            gaps = np.nonzero(np.diff(hit) > 1)[0]
            starts = np.concatenate(([0], gaps + 1))
            for st in starts:
                if hit.size - st >= run and hit[st + run - 1] - hit[st] == run - 1:
                    out[x] = y0 + int(hit[st])
                    break
        return out

    def fit(prof, label, expect, deg):
        xs = np.array(sorted(prof), dtype=np.float64)
        ys = np.array([prof[x] for x in sorted(prof)], dtype=np.float64)
        if xs.size < 60:
            print("  %-10s 列数不足(%d), 跳过" % (label, xs.size)); return
        c = np.polyfit(xs, ys, deg)
        r = ys - np.polyval(c, xs)
        rms = float(np.sqrt((r ** 2).mean()))
        print("  %-10s 列数 %4d 跨度 %.0fpx  去%d次趋势后: RMS %.2fpx  p95 %.2fpx  max %.2fpx "
              "(nominal %.2fpx)" % (label, xs.size, xs.max() - xs.min(), deg,
                                    rms, float(np.percentile(np.abs(r), 95)),
                                    float(np.abs(r).max()), expect))
        return r

    up_top = int(round(img.height - w.to_window(w._cx, w._upper_y_c + Ri)[1])) + 6
    prof_u = scan(range(max(0, cx - 430), min(img.width, cx + 430)), up_top, up_top + int(2 * Ri))
    print("上球沙面 (1px/列, 二次去趋势):")
    fit(prof_u, "upper", Ri * 2 * 0.0014, 2)

    lo_top = int(round(img.height - w.to_window(w._cx, w._lower_y_c + Ri)[1])) + 6
    prof_l = scan(range(max(0, cx - 430), min(img.width, cx + 430)), lo_top, lo_top + int(2 * Ri))
    print("下球沙堆 (1px/列, 一次去趋势; 剔掉 |x-cx|<80 的颈口区):")
    left = {x: y for x, y in prof_l.items() if x < cx - 80}
    right = {x: y for x, y in prof_l.items() if x > cx + 80}
    fit(left, "mound-L", Ri * 2 * 0.004, 1)
    fit(right, "mound-R", Ri * 2 * 0.004, 1)

    # 顶点: 中心 ±80px 之内, 剔掉颈口后剩下的最高点附近轮廓
    near = {x: y for x, y in prof_l.items() if 80 <= abs(x - cx) <= 150}
    if near:
        sxs = sorted(near)
        seq = [near[x] for x in sxs[:8]] + ["..."] + [near[x] for x in sxs[-8:]]
        print("   距中轴 80~150px 的轮廓: %s" % (seq,))
    print("   下球沙堆左右两条各自的最小/最大 y: L %s  R %s"
          % ((min(left.values()), max(left.values())) if left else None,
             (min(right.values()), max(right.values())) if right else None))


if __name__ == "__main__":
    sys.exit(main())
