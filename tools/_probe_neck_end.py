# -*- coding: utf-8 -*-
"""50s 档**末段**颈部到底有没有沙 —— 用户 2026-10-06 报的"必现 bug"。

用户原话:「在持续时间是 **50s** 的沙漏**快结束的一段时间内**, 沙漏**中间的颈部是没有沙子**的」。

本探针把指定周期的 elapsed 逐点推到末尾, 每一帧同时读:
  · `_neck_sand_side()` 的返回**(应用自己的量)** —— 长度 0 就是"应用认为颈部是空的"
  · **窄沙柱行数**(从**画出来的图**里按沙色自己找, 不猜坐标)
  · 上球沙面高度 / 沙堆高度

跑法: python tools/_probe_neck_end.py [周期] [时刻1,时刻2,...]
      HG_SHOTDIR=r38_before python tools/_probe_neck_end.py 50 ...
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SHOTDIR = os.environ.get("HG_SHOTDIR", "r38")
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
TIMES = ([float(v) for v in sys.argv[2].split(",")] if len(sys.argv) > 2
         else [0.5, 10.0, 30.0, 40.0, 44.0, 46.0, 47.0, 48.0, 48.5, 49.0,
               49.3, 49.5, 49.7, 49.9, 50.0])


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    sys.argv = [sys.argv[0], "_neck_end"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
    from PIL import Image
    import numpy as np

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None

    shots = {}

    def grab():
        w, h = int(Window.width), int(Window.height)
        px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
        return np.asarray(Image.frombytes("RGBA", (w, h), px)
                          .transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.3)

        def go(self, _dt):
            hg = self.hourglass
            hg.duration = PERIOD
            hg._rebuild_height_table()
            hg.reset()
            self.pending = list(TIMES)
            self.step()

        def step(self, *_a):
            """每个时刻走两步, **中间隔一帧**。

            🔴 **本探针 v1 的死因**(2026-10-06): `redraw()` 之后**立刻** `glReadPixels`,
            读到的还是**上一帧**的帧缓冲 —— 12 张图全是同一个"50/50 满球"画面。
            两臂比出来的"完全一致"是**读数假象**, 不是结论。
            Kivy 在同一次回调里只改指令、不重绘; 必须 `ask_update()` 让出到下一帧再读。
            """
            Clock.unschedule(self.step)
            if not self.pending:
                Clock.schedule_once(lambda dt: self.stop(), 0.3)
                return
            t = self.pending[0]
            hg = self.hourglass
            hg.elapsed = t
            hg.running = True      # 不加暂停遮罩(0.55 alpha 会把沙色冲淡到掩膜失效)
            hg.redraw()
            hg.running = False
            Window.canvas.ask_update()
            Clock.schedule_once(lambda dt: self.read(), 0.0)

        def read(self, *_a):
            import numpy as _np
            hg = self.hourglass
            t = self.pending.pop(0)
            img = grab()
            a = img.astype(int)
            # 沙 = 暖色(R 明显大于 B)。**不要用"离 sand_base 的距离"** ——
            # 颈部沙柱叠了渐变纹理(NECK_UV), 颜色会明显偏离 base, 掩膜会漏。
            near = (a[:, :, 0] - a[:, :, 2]) > 60
            # **窄沙柱行数**: 以沙流轴线为中心逐行量沙色游程宽度, 宽度 ∈ [1,25] px
            # 的行 = "颈部/沙流"的行(球腔里那些行宽几百 px, 自然被排除)。
            # ⚠️ 不能写成"找最长连续段" —— 那会把上喇叭口(一路收窄、且**恒满**)
            #    整段算进来, 得到一个**不随状态变化**的常数(踩过: 恒 80)。
            # ⚠️ 也不能拿 `_upper_sand_bot` 当图像行号 —— 那是 Kivy 屏幕坐标(y 向上),
            #    与左上原点的窗口像素差一个 canvas 偏移(踩过: 恒 4789, 其实量的下球)。
            cx = a.shape[1] // 2
            narrow = 0
            for y in range(a.shape[0]):
                xs = _np.nonzero(near[y, cx - 40:cx + 40])[0]
                if len(xs) and 1 <= (xs.max() - xs.min() + 1) <= 25:
                    narrow += 1
            shots[t] = dict(side=len(hg._neck_sand_side()), narrow=int(narrow),
                            upper=float(hg._upper_sand_height_px()),
                            mound=float(hg._mound_height_px()), pn=int(hg.pn))
            Image.fromarray(img).save(ROOT / "_shot" / SHOTDIR
                                      / ("neck_t%05.1f.png" % t))
            Clock.schedule_once(self.step, 0.05)
    P().run()
    return shots


if __name__ == "__main__":
    shots = run()
    print("")
    print("  === 周期 %.1fs: 末段颈部 ===  (图片存 _shot/%s/)" % (PERIOD, SHOTDIR))
    print("  %-8s %-10s %-13s %-10s %-10s %s"
          % ("elapsed", "side点数", "窄沙柱行数", "上球高", "沙堆高", "在途粒子"))
    for t in TIMES:
        d = shots.get(t)
        if not d:
            continue
        flag = "  **应用认为颈部是空的**" if d["side"] == 0 else ""
        print("  %-8.1f %-10d %-13d %-10.1f %-10.1f %d%s"
              % (t, d["side"], d["narrow"], d["upper"], d["mound"], d["pn"], flag))
    print("")
