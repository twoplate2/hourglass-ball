# -*- coding: utf-8 -*-
"""**上球沙面**起伏的逐列轮廓 —— 用户 2026-10-06:「起伏放到最大还是有一个锐角」。

为什么要专门写一个: 已有的 `tools/_probe_dev_levels.py` 把统计窗**写死在 400x800 的坐标上**
(y[60:340] x[60:340]), 而实际窗口尺寸会变(实测过 500x1000 / 400x800 两种) ⇒ **裁错地方**,
量的根本不是沙面。这里**自动定位沙面**再量。

产出:
  · 每个档位一张裁图(沙面放大), 存 _shot/rough/<档>.png
  · **逐列沙面 y** 的序列, 算"折角"指标:
      `二阶差分` 的分位(p95/p99/max) —— 线性插值下, 折角 ⇔ 二阶差分大。
    圆滑曲线该是**连续的小值**; 锯齿会在节点处出尖峰。
    ⚠️ 对照臂 = **同一档跑两次**(应逐像素 0 差异), 证明量具本身不抖。

跑法: python tools/_probe_upper_rough.py            # 扫 1..6
      python tools/_probe_upper_rough.py 1 6        # 只跑指定档
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "_shot" / "rough"

LEVELS = ("1", "2", "3", "4", "5", "6")


def run(levels):
    os.environ.pop("KIVY_METRICS_DENSITY", None)   # 桌面真实密度(别用设备模拟污染布局)
    sys.argv = [sys.argv[0], "_upper_rough"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
    from PIL import Image
    import numpy as np

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 50}
    m.HourglassWidget.save_config = lambda *_: None

    shots = {}
    todo = list(levels) + [levels[len(levels) // 2]]      # 末尾重跑一次做对照臂

    def shot():
        w, h = int(Window.width), int(Window.height)
        px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
        return np.asarray(Image.frombytes("RGBA", (w, h), px)
                          .transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.begin, 1.3)

        def begin(self, _dt):
            self.hg = self.hourglass
            self.hg.set_duration(10.0)
            self.hg._rebuild_height_table()
            self.hg.reset()
            self.hg.toggle()
            Clock.schedule_once(self.next_level, 4.0)     # 跑到中段(起伏包络非 0)

        def next_level(self, *_):
            if not todo:
                Clock.schedule_once(lambda dt: self.stop(), 0.3)
                return
            lv = todo.pop(0)
            self.hg.set_rough_level(lv)
            Clock.schedule_once(lambda dt: self.grab(lv), 0.5)

        def grab(self, lv):
            img = shot()
            shots.setdefault(lv, []).append(img)
            OUT.mkdir(parents=True, exist_ok=True)
            Image.fromarray(img).save(OUT / ("%s_%d.png" % (lv, len(shots[lv]) - 1)))
            self.next_level()

    P().run()
    return shots


def surface_profile(img, x0, x1):
    """逐列找**上球沙面**的 y(沙色最上面那个像素), 限定在画布上半部。"""
    import numpy as np
    a = img.astype(int)
    sand = (a[:, :, 0] - a[:, :, 2]) > 70
    h = a.shape[0]
    lo, hi = int(h * 0.18), int(h * 0.60)          # 只看上球那一段
    prof = []
    for x in range(x0, x1):
        col = np.nonzero(sand[lo:hi, x])[0]
        prof.append(col[0] + lo if len(col) else np.nan)
    return np.asarray(prof, dtype=float)


def angle_metric(prof):
    """折角指标: 二阶差分(曲率)的分位。圆滑曲线是小值连续; 锯齿在节点处出尖峰。"""
    import numpy as np
    p = prof[~np.isnan(prof)]
    if len(p) < 20:
        return None
    d2 = np.abs(np.diff(p, 2))
    return (float(np.percentile(d2, 50)), float(np.percentile(d2, 95)),
            float(np.percentile(d2, 99)), float(d2.max()))


if __name__ == "__main__":
    levels = tuple(sys.argv[1:]) or LEVELS
    shots = run(levels)
    print("")
    print("  === 上球沙面: 折角指标(二阶差分 |曲率|, 单位 px) ===")
    print("  %-5s %-9s %-9s %-9s %-9s %s"
          % ("档", "p50", "p95", "p99", "max", "裁图"))
    for lv in levels:
        arr = shots.get(lv) or []
        if not arr:
            print("  %-5s 没采到" % lv); continue
        img = arr[0]
        h, w = img.shape[:2]
        prof = surface_profile(img, int(w * 0.2), int(w * 0.8))
        mt = angle_metric(prof)
        if mt is None:
            print("  %-5s 找不到沙面" % lv); continue
        print("  %-5s %-9.2f %-9.2f %-9.2f %-9.2f _shot/rough/%s_0.png"
              % (lv, mt[0], mt[1], mt[2], mt[3], lv))
    # 对照臂
    ref = levels[len(levels) // 2]
    if len(shots.get(ref, [])) > 1:
        import numpy as np
        a, b = shots[ref][0], shots[ref][1]
        d = np.abs(a.astype(int) - b.astype(int)).max()
        print("")
        print("  ⚠️ 对照臂(档 %s 跑两次): 最大通道差 %d %s"
              % (ref, d, "✓ 量具不抖" if d == 0 else "**量具本身在抖**"))
    print("")
    print("  读法: **p99/max 远大于 p50** ⇒ 轮廓里有孤立的尖角(锯齿);")
    print("        圆滑曲线应当是 p50≈p95≈p99, 没有离群的尖峰。")
