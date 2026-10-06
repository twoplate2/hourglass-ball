# -*- coding: utf-8 -*-
"""F2 验收: 弹窗的**标题**与**「确定」键**是否完整在屏内(窄盒/分屏场景)。

判据(配方原话): 「360 / 400 / 460 / 493 / 540 dp 五档断言: 标题在屏内 且 确定键完整在屏内」。
本探针不用颜色找 —— 直接从 **widget 树的几何**上量, 并用 `to_window` 把两角都变换过去,
**横屏(挂在反旋转层上)也算得对**(取变换后两角的包围盒, 与旋转角无关)。

跑法(一进程一例):
  python tools/_r37_f2_fit.py <框边长dp> [duration|dev]
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    dpside = int(sys.argv[1]) if len(sys.argv) > 1 else 360
    which = sys.argv[2] if len(sys.argv) > 2 else "duration"
    dens = 3.0
    px = int(round(dpside * dens))
    os.environ["KIVY_METRICS_DENSITY"] = str(dens)
    sys.argv = [sys.argv[0], "f2", "--landscape"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.uix.button import Button
    from kivy.uix.label import Label

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 50}
    m.HourglassWidget.save_config = lambda *_: None

    res = {}

    def walk(w, out, d=0):
        out.append(w)
        if d < 12:
            for c in getattr(w, "children", []):
                walk(c, out, d + 1)
        return out

    def bbox(w):
        x0, y0 = w.to_window(w.x, w.y)
        x1, y1 = w.to_window(w.x + w.width, w.y + w.height)
        return (min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1))

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (px, px)
            Clock.schedule_once(self.begin, 1.4)

        def begin(self, _dt):
            res["got"] = (int(Window.width), int(Window.height))
            if which == "dev":
                self._open_dev_menu()
            else:
                self.on_duration_picker(None)
            Clock.schedule_once(self.take, 1.2)

        def take(self, _dt):
            ws = walk(Window, [])
            pop = None
            for w in ws:
                if type(w).__name__ == "_SandBgPopup":
                    pop = w
                    break
            W, H = int(Window.width), int(Window.height)
            sub = walk(pop, []) if pop is not None else []
            title = None
            for w in sub:
                if isinstance(w, Label) and str(w.text) == str(getattr(pop, "title", "")):
                    title = w
                    break
            ok_btn = None
            for w in sub:
                if isinstance(w, Button) and str(w.text) == "确定":
                    ok_btn = w
                    break
            res["pop_size"] = tuple(map(int, pop.size)) if pop is not None else None
            res["title"] = bbox(title) if title is not None else None
            res["ok"] = bbox(ok_btn) if ok_btn is not None else None
            res["win"] = (W, H)
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    return res


def verdict(name, box, W, H):
    if box is None:
        return "**找不到**"
    x0, x1, y0, y1 = box
    inside = (x0 >= -0.5 and x1 <= W + 0.5 and y0 >= -0.5 and y1 <= H + 0.5)
    return "%s x %.0f~%.0f y %.0f~%.0f  %s" % (name, x0, x1, y0, y1,
                                               "在屏内" if inside else "**出屏**")


if __name__ == "__main__":
    r = main()
    W, H = r.get("win", (0, 0))
    print("")
    print("  盒 %dx%d (请求 %s, 实得 %s)  弹窗 %s" %
          (W, H, sys.argv[1] + "dp", r.get("got"), r.get("pop_size")))
    t = verdict("标题  ", r.get("title"), W, H)
    o = verdict("确定键", r.get("ok"), W, H)
    print("  %s" % t)
    print("  %s" % o)
    good = (r.get("title") is not None and r.get("ok") is not None
            and "出屏" not in t and "出屏" not in o)
    print("  => %s" % ("**通过**(标题与确定键都完整在屏内)" if good else "**不通过**"))
    raise SystemExit(0 if good else 1)
