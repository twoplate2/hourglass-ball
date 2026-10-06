# -*- coding: utf-8 -*-
"""转屏时"已开着的弹窗"有没有跟着换宿主 (2026-10-06 r32-1号 抓到的 E1 缺陷)。

缺陷: `_SandBgPopup.open()` 在**开的那一刻**按 `layer.angle` 定宿主 ——
竖屏挂 `Window`(Window 不旋转) / 横屏挂旋转层(随层转)。转屏只改 `layer.angle`,
**没有任何地方重挂已开的弹窗** ⇒ 竖屏开着弹窗再转横屏, 弹窗**侧躺 90°**。

判据(**先标定**: 下面两组都要跑, 且必须给出不同的读数):
  【已知对】横屏开弹窗 → 转竖屏: 转后弹窗应当**方向正确**且宽度回到原生竖屏值。
  【已知错】竖屏开弹窗 → 转横屏: 修前宿主仍是 Window(不旋转) ⇒ 侧躺;
                            修后宿主应换成旋转层 ⇒ 正立。

结构判据(不靠像素): 转屏后 `popup._window is LandLayer` ⟺ 它随层旋转。
像素判据(防"结构对了但画出来不对"): 存三张图, 人眼复核。

跑法: python tools/_probe_popup_rehost.py <case>
  case ∈ portrait_to_land | land_to_portrait
"""
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "_shot" / "rehost"
# ⚠️ 文件名必须带标签 —— 否则"改前"那轮会被"改后"那轮**同名覆盖**,
#    之后看图的人以为看的是另一个状态(上一轮同类探针刚踩过)
TAG = os.environ.get("HG_TAG", "")


def run_case(case):
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    if "--landscape" not in sys.argv:
        sys.argv.append("--landscape")          # 桌面要这个才会走反旋转分支
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
    now = [1000.0]
    m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

    PORT, LAND = (1080, 2400), (2400, 1080)
    first, second = (PORT, LAND) if case == "portrait_to_land" else (LAND, PORT)
    res = {"case": case}

    def shot(tag):
        ww, hh = int(Window.width), int(Window.height)
        px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
        img = np.asarray(Image.frombytes("RGBA", (ww, hh), px)
                         .transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))
        OUT.mkdir(parents=True, exist_ok=True)
        Image.fromarray(img).save(OUT / ("%s%s_%s.png" % (case, TAG, tag)))
        return img

    def snap(app, label):
        pop = None
        for c in Window.children:
            if type(c).__name__ == "_SandBgPopup":
                pop = c
                break
        if pop is None:
            layer = m._land_layer()
            for c in (layer.children if layer is not None else []):
                if type(c).__name__ == "_SandBgPopup":
                    pop = c
                    break
        layer = m._land_layer()
        d = {
            "label": label,
            "win": (int(Window.width), int(Window.height)),
            "angle": getattr(layer, "angle", None),
            "host": (type(pop._window).__name__ if (pop is not None and pop._window is not None)
                     else None),
            "pop_size": (int(pop.size[0]), int(pop.size[1])) if pop is not None else None,
            "sh": tuple(pop.size_hint) if pop is not None else None,
        }
        res.setdefault("steps", []).append(d)
        return d

    class P(m.HourglassApp):
        def on_start(self):
            Clock.schedule_once(self.step1, 1.2)

        def step1(self, _dt):
            Window.size = first
            Clock.schedule_once(self.step2, 1.0)

        def step2(self, _dt):
            got = (int(Window.width), int(Window.height))
            if got != first:
                res["error"] = "MISMATCH 要 %s 实得 %s" % (first, got)
                self.stop()
                return
            self.on_duration_picker(None)          # 在**第一个**朝向下开弹窗
            Clock.schedule_once(self.step3, 0.9)

        def step3(self, _dt):
            snap(self, "开窗于 %s" % (first,))
            shot("A_open")
            Window.size = second                   # ★ 转屏(不重启 app)
            Clock.schedule_once(self.step4, 1.6)   # 等 _frame 跑到 apply_orientation

        def step4(self, _dt):
            snap(self, "转到 %s" % (second,))
            shot("B_rotated")
            Window.size = first                    # 再转回来
            Clock.schedule_once(self.step5, 1.6)

        def step5(self, _dt):
            snap(self, "再转回 %s" % (first,))
            shot("C_back")
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    return res


def main():
    case = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
        else "portrait_to_land"
    # ⚠️ argv 里已经有 "--landscape" 时上面的取参要跳过它
    cand = [a for a in sys.argv[1:] if not a.startswith("-")]
    case = cand[0] if cand else "portrait_to_land"
    r = run_case(case)
    print("")
    print("  === %s ===" % case)
    if r.get("error"):
        print("  !! %s" % r["error"])
    for s in r.get("steps", []):
        print("  %-22s 窗 %-12s angle=%-4s 宿主=%-14s 弹窗=%-12s size_hint=%s"
              % (s["label"], "%dx%d" % s["win"], s["angle"], s["host"],
                 "%dx%d" % s["pop_size"] if s["pop_size"] else "-", s["sh"]))
    # 判据(⚠️ 两个朝向要分开判, 别把非缺陷算成"修好了"):
    #   angle != 0 ⇒ 宿主**必须**是 LandLayer。挂在 Window 上就是**侧躺**(可见缺陷)。
    #   angle == 0 ⇒ 挂在层上也不转, **画出来是对的** ⇒ 这一向不算缺陷;
    #                修后归一到 Window 只是"回到原生宿主", 属一致性, 不是修复。
    steps = r.get("steps", [])
    if len(steps) >= 2:
        s1 = steps[1]
        ang = s1["angle"]
        if ang:
            ok = (s1["host"] == "LandLayer")
            print("")
            print("  ★ angle=%s ⇒ 宿主必须是 LandLayer, 实得 %s  =>  %s"
                  % (ang, s1["host"],
                     "**正确(随层转)**" if ok else "**侧躺 —— 可见缺陷**"))
        else:
            print("")
            print("  ★ angle=0 ⇒ 宿主挂哪儿都不转, **画出来都对**(这一向本就不是缺陷);"
                  " 实得 %s%s" % (s1["host"],
                                  "(已归一到原生 Window)" if s1["host"] == "WindowSDL"
                                  else "(仍在层上; 与原生等价)"))
    print("  图: _shot/rehost/%s_{A_open,B_rotated,C_back}.png" % case)


if __name__ == "__main__":
    main()
