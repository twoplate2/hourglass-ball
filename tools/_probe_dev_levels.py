# -*- coding: utf-8 -*-
"""设置画面环节：「沙面起伏 / 沙体颗粒」六档，**上球沙面看起来到底有没有差别**。

为什么要量这个：开发者菜单是**刻意贴底摆**的（留上半屏给沙漏当预览），
所以"改一档能不能当场看出变化"就是这个界面的**唯一功能**。
若六档画出来几乎一样 —— 那是一个"不符合直觉、影响体验"的东西（旋钮转了没反应）。

判据(先标定)：在**同一时刻同一帧**取上半球沙面那一带，算
  ① 逐像素与"出厂默认档(D/4)"的最大通道差、差异像素数
  ② 沙面轮廓的**竖向抖动幅度**（沿 x 逐列找沙面 y，取 std）
已知对照臂：**同一档跑两次**（应当逐像素 0 差异）—— 证明量具本身不抖。

跑法: python tools/_probe_dev_levels.py <rough|grain>
"""
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "_shot" / "levels"


def run(kind):
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    sys.argv = [sys.argv[0], "_lv"]
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

    # SURFACE_ROUGH_LEVELS 的行是 ("1", 0.0014) 两元组;
    # SAND_GRAIN_LEVELS 的行是 ("1", 0.10, 1) 三元组 —— 别写成同一个解包
    if kind == "rough":
        levels = [row[0] for row in m.SURFACE_ROUGH_LEVELS]
    else:
        levels = [row[0] for row in m.SAND_GRAIN_LEVELS]
    shots = {}

    def grab():
        w, h = int(Window.width), int(Window.height)
        px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
        return np.asarray(Image.frombytes("RGBA", (w, h), px)
                          .transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            # 🔴 必须在**中途**量: 闸门里写着「idle / done 两端 `rough` 包络为 0(crest=0)」——
            #    静止满球时这个档位**按设计就没有效果**, 在那儿量什么都量不到。
            #    所以: 跑起来 -> 到中途 -> **暂停**(粒子与沙面冻结) -> 再扫六档。
            self.hourglass.duration = 10.0
            self.hourglass._rebuild_height_table()
            self.hourglass.toggle()                     # 开始
            Clock.schedule_once(self.pause, 4.0)        # 到中途

        def pause(self, _dt):
            self.hourglass.toggle()                     # 暂停
            self._seq = list(levels) + [levels[len(levels) // 2]]  # 末尾重跑一次做对照
            self._i = 0
            Clock.schedule_once(self._next, 0.4)

        def _next(self, *_):
            if self._i >= len(self._seq):
                Clock.schedule_once(lambda dt: self.stop(), 0.3)
                return
            lv = self._seq[self._i]
            self._i += 1
            hg = self.hourglass
            if kind == "rough":
                hg.set_rough_level(lv)
            else:
                hg.set_grain_level(lv)
            Clock.schedule_once(lambda dt: self._shot(lv), 0.45)

        def _shot(self, lv):
            shots.setdefault(lv, []).append(grab())
            OUT.mkdir(parents=True, exist_ok=True)
            Image.fromarray(grab()).save(
                OUT / ("%s_%s_%d.png" % (kind, lv, len(shots[lv]) - 1)))
            self._next()

    P().run()
    return levels, shots


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "rough"
    OUT.mkdir(parents=True, exist_ok=True)
    import numpy as np
    from PIL import Image
    levels, shots = run(kind)
    ref_lv = levels[len(levels) // 2]
    ref = shots[ref_lv][0]
    print("")
    print("  === %s 六档：上球沙面那一带 ===" % kind)
    print("  参照档 = %s（出厂默认）" % ref_lv)
    # 上半球沙面附近：画布 400x800，上球 y_img 大约 60~330
    y0, y1, x0, x1 = 60, 340, 60, 340
    print("  统计窗 y[%d:%d] x[%d:%d]" % (y0, y1, x0, x1))
    print("  %-6s %-10s %-12s %s" % ("档", "差异像素", "最大通道差", "抖动 std"))
    for lv in levels:
        a = shots[lv][0][y0:y1, x0:x1].astype(int)
        r = ref[y0:y1, x0:x1].astype(int)
        d = np.abs(a - r).max(axis=2)
        # 沙面轮廓: 逐列找最上面那个"沙"像素 (R-B>60)
        prof = []
        for x in range(a.shape[1]):
            col = a[:, x]
            idx = np.nonzero((col[:, 0] - col[:, 2]) > 60)[0]
            prof.append(idx[0] if len(idx) else np.nan)
        prof = np.array(prof, dtype=float)
        prof = prof[~np.isnan(prof)]
        # ⚠️ 直接取 std 是被**球面曲率**主导的(六档都是 113), 那是废数。
        #    要的是**抖动振幅**: 减掉重平滑(窗口 41px)后的残差 std。
        if len(prof) > 60:
            k = 41
            sm = np.convolve(prof, np.ones(k) / k, mode="valid")
            res = prof[k // 2: k // 2 + len(sm)] - sm
            std = float(res.std())
            amp = float(np.percentile(np.abs(res), 95))
        else:
            std = amp = float("nan")
        print("  %-6s %-10d %-12d %.2f px  (p95 %.2f px)" % (lv, int((d > 8).sum()), int(d.max()), std, amp))
    # 对照臂: 同一档两次
    if len(shots[ref_lv]) > 1:
        a = shots[ref_lv][0][y0:y1, x0:x1].astype(int)
        b = shots[ref_lv][1][y0:y1, x0:x1].astype(int)
        d = np.abs(a - b).max(axis=2)
        print("  ⚠️ 对照臂（同一档 %s 跑两次）：差异像素 %d，最大通道差 %d"
              % (ref_lv, int((d > 8).sum()), int(d.max())))
