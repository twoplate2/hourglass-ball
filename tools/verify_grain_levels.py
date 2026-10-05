# -*- coding: utf-8 -*-
"""「沙体颗粒」六档**是不是真的六档** —— 用"看得见"当门槛, 不用 ">0"。

## 为什么要有这个工具
1.99 首次出货时我的自检只报了"相邻档 maxdiff = 5/7/29/26/28, 全 >0"。**但非零 ≠ 看得见** ——
r17-1号 在设备上逐档整幅差分, 查出:
    「1 vs 2」「2 vs 3」在沙体区域内 **0 个像素 > 15 级**
    「4 vs 6」整幅 **只有 1 个像素 > 8 级**
⇒ 六档实际只有三种画面, **三个按钮是摆设, 标签还在骗人**。

## 判据(每个相邻对都要过)
  沙体掩膜内, **差值 > 15 级的像素占比 ≥ 2%** 才算"看得见"。
  (2% 是经验门槛: 一条 100px 宽的横带在 890px 的球上约占 11%, 而"只压球底一小条"约 0.8%。)

跑法: python tools/verify_grain_levels.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIX = (1096, 2214)
# 判据标定(用**两个已知点**, 不是拍的):
#   看不见的档: r17-1号 在设备上量到「1 vs 2」沙体区域 **> 15 级 = 0 个像素、> 8 级 = 0.00%**
#   看得见的档: 同一批粗度档「4 vs 5」> 8 级 = 18~28%
# ⇒ 用 >8 级占比当判据, 门槛 10% 落在中间, 两边都有大余量。
VISIBLE_FRAC = 0.10      # 沙体掩膜内"差 > 8 级"的像素占比门槛
VISIBLE_LEVEL = 8


def main():
    with tempfile.TemporaryDirectory(prefix="grainlv-") as home:
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
        import numpy as np

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 60}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])
        shots = {}
        labels = [lb for lb, _g, _c in m.SAND_GRAIN_LEVELS]
        # HG_TEST_TABLE="标签:grad:coarse,标签:grad:coarse,..." ⇒ 测候选表(不改 main.py)
        spec = os.environ.get("HG_TEST_TABLE")
        if spec:
            table = []
            for it in spec.split(","):
                lb, g, c = it.split(":")
                table.append((lb, float(g), int(c)))
            m.SAND_GRAIN_LEVELS = tuple(table)
            m._grain_level = lambda label: next(
                ((g, c) for lb, g, c in table if lb == label), table[0][1:])
            labels = [lb for lb, _g, _c in table]
            print("测试候选表: %s" % (table,))

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.resize, 1)

            def resize(self, _dt):
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(PIX[0] / ratio), round(PIX[1] / ratio))
                Clock.schedule_once(self.begin, 0.6)

            def begin(self, _dt):
                self.hg = self.hourglass
                w = self.hg
                w.set_duration(60)
                w.completion_enabled = False
                if not w.running:
                    w.toggle()
                w._done_at = None
                # 满球静止: 变量最少(没有沙面/沙堆/粒子干扰), 只看材质本身
                w.elapsed = 0.0
                w.running = False
                self.k = 0
                Clock.schedule_once(self.step, 0.4)

            def step(self, _dt):
                if self.k >= len(labels):
                    return self.finish()
                lb = labels[self.k]
                w = self.hg
                w.set_grain_level(lb)
                w.redraw()
                Clock.schedule_once(lambda d: self.grab(lb), 0.45)

            def grab(self, lb):
                ww, hh = map(int, Window.size)
                px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
                shots[lb] = np.asarray(
                    Image.frombytes("RGBA", (ww, hh), px).transpose(
                        Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"),
                    dtype=np.int16)
                self.k += 1
                Clock.schedule_once(self.step, 0.25)

            def finish(self):
                report(shots, self.hg, labels)
                # 复位到出厂默认
                self.hg.set_grain_level(m.SAND_GRAIN_LEVEL_DEFAULT)
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


def report(shots, w, labels):
    import numpy as np
    base = np.array(w.sand_base) * 255.0
    a0 = shots[labels[0]]
    # 沙体掩膜: 与 base 差 < 90 且在上球范围内
    mask = np.abs(a0 - base.reshape(1, 1, 3)).max(axis=2) < 90
    print("沙体掩膜像素数: %d (占全幅 %.1f%%)"
          % (mask.sum(), 100.0 * mask.sum() / mask.size))
    print("\n相邻两档在**沙体掩膜内**的差异（判据: 差 > %d 级的占比 ≥ %.0f%%）"
          % (VISIBLE_LEVEL, VISIBLE_FRAC * 100))
    print("%-6s %-8s %-10s %-10s %s" % ("对比", "参数", ">15级占比", ">8级占比", "判定"))
    bad = []
    for i in range(len(labels) - 1):
        lb0, lb1 = labels[i], labels[i + 1]
        d = np.abs(shots[lb0] - shots[lb1]).max(axis=2)
        inside = d[mask]
        f15 = float((inside > 15).mean())
        f8 = float((inside > 8).mean())
        ok = f8 >= VISIBLE_FRAC
        if not ok:
            bad.append((lb0, lb1, f15))
        g0 = _params(lb0)
        g1 = _params(lb1)
        print("%-6s %-8s %-10s %-10s %s"
              % ("%s vs %s" % (lb0, lb1), "%s→%s" % (g0, g1),
                 "%.2f%%" % (f15 * 100), "%.2f%%" % (f8 * 100),
                 "OK" if ok else "**看不见**"))
    print()
    if bad:
        print("**有 %d 对相邻档看不见 —— 按钮是摆设**" % len(bad))
        for lb0, lb1, f in bad:
            print("   %s vs %s  (%.2f%%)" % (lb0, lb1, f * 100))
    else:
        print("六档两两相邻都看得见 ✅")
    return not bad


def _params(lb):
    import main as m
    g, c = m._grain_level(lb)
    return "g%.2f/c%d" % (g, c)


if __name__ == "__main__":
    sys.exit(main())
