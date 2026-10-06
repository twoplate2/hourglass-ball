# -*- coding: utf-8 -*-
"""配方③ 的 A/B 抓图: 在**指定的 popup / 窗口尺寸**下截一张全屏图, 存成 <tag>_<case>.png。

配套 `tools/_r37_ab_compare.py` 做逐像素比对 + 并排图。
先跑"改前"(把 main.py 换成 HEAD 版), 再跑"改后", 两次用不同的 --tag。

跑法(一进程一例, Kivy 单例):
  python tools/_r37_ab_capture.py <tag> <case>
  case ∈ land_duration | port_duration | land_dev | port_dev | land_sound
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "_shot" / "r37"

CASES = {
    #           窗口          landscape  dens   popup
    "land_duration": ((2400, 1080), True,  1.75, "duration"),
    "port_duration": ((1080, 2400), False, 1.75, "duration"),
    "land_dev":      ((2400, 1080), True,  1.75, "dev"),
    "port_dev":      ((1080, 2400), False, 1.75, "dev"),
    "land_sound":    ((2400, 1080), True,  1.75, "sound"),
    # 配方② 底栏: 不弹窗, 只看主界面(密度 480 = 3.0 对应真机 360/320/411 dp)
    "bar_320":       ((960, 1600),  False, 3.0,  "none"),
    "bar_360":       ((1080, 1920), False, 3.0,  "none"),
    "bar_411":       ((1233, 2160), False, 3.0,  "none"),
    "bar_A":         ((1080, 2400), False, 1.75, "none"),
    # 配方① F2: 360dp 方盒(分屏/自由窗口), 弹窗自然高 493dp > 盒 360dp ⇒ 改前两个按钮出屏
    "f2_360_duration": ((1080, 1080), True, 3.0, "duration"),
    "f2_360_dev":      ((1080, 1080), True, 3.0, "dev"),
}


def run(tag, case):
    win, land, dens, popup = CASES[case]
    os.environ["KIVY_METRICS_DENSITY"] = str(dens)
    if land:
        sys.argv = [sys.argv[0], case, "--landscape"]
    else:
        sys.argv = [sys.argv[0], case]
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

    res = {}

    def shot():
        ww, hh = int(Window.width), int(Window.height)
        px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
        return np.asarray(Image.frombytes("RGBA", (ww, hh), px)
                          .transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = win
            Clock.schedule_once(self.s1, 1.4)

        def s1(self, _dt):
            got = (int(Window.width), int(Window.height))
            res["mismatch"] = (got != win)
            if popup == "dev":
                self._open_dev_menu()
            elif popup == "sound":
                self.on_sound_picker()
            elif popup == "duration":
                self.on_duration_picker(None)
            Clock.schedule_once(self.s2, 1.0)

        def s2(self, _dt):
            img = shot()
            OUT.mkdir(parents=True, exist_ok=True)
            Image.fromarray(img).save(OUT / ("%s_%s.png" % (tag, case)))
            res["img"] = img
            res["pop"] = None
            for w in list(Window.children) + list(self.root.children):
                if type(w).__name__ == "_SandBgPopup":
                    res["pop"] = (tuple(map(int, w.pos)), tuple(map(int, w.size)))
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    return res, win


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "after"
    case = sys.argv[2] if len(sys.argv) > 2 else "land_duration"
    r, win = run(tag, case)
    print("  captured %s_%s.png  want=%s  popup=%s%s"
          % (tag, case, win, r.get("pop"),
             "  **MISMATCH**" if r.get("mismatch") else ""))
