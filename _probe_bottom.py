# -*- coding: utf-8 -*-
"""底栏**到底溢没溢出**, 以及溢出的机制 —— 直接读运行时的真实几何。

起因(2026-10-06 用户实测截图): 底栏 `50秒 / 沙沙声 / v1.154 / 开始 / 重置` **互相重叠**,
顶部 6 个色块文字也挤在一起。同一份代码在我这儿(density=1.0, 400x800)是好的 ⇒
**差的是运行时密度或窗宽**, 不是代码本身"必然坏"。
本脚本把这两件事一起打出来, 免得再猜。

跑法: python _probe_bottom.py [宽] [高]
      (不传则用 app 自己设的尺寸)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import main as m                                    # noqa: E402
if len(sys.argv) > 3:                               # 可选: 指定另一份 main 来对比
    import importlib.util
    _src = Path(sys.argv[3]).resolve()
    _spec = importlib.util.spec_from_file_location("main_alt", _src)
    m = importlib.util.module_from_spec(_spec)
    sys.modules["main_alt"] = m
    _spec.loader.exec_module(m)
    print("  [源] 用 %s" % _src.name)
from kivy.clock import Clock                        # noqa: E402
from kivy.core.window import Window                 # noqa: E402
from kivy.metrics import Metrics, dp, sp            # noqa: E402
from kivy.uix.button import Button                  # noqa: E402
from kivy.uix.boxlayout import BoxLayout            # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *_: None
m.HourglassWidget._make_completion_sound = lambda *_: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 50}
m.HourglassWidget.save_config = lambda *_: None

want = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else None


def walk(wd, out, d=0):
    out.append(wd)
    if d > 10:
        return out
    for c in getattr(wd, "children", []):
        walk(c, out, d + 1)
    return out


class P(m.HourglassApp):
    def on_start(self):
        if want:
            Window.size = want
        Clock.schedule_once(self.report, 1.4)

    def report(self, _dt):
        print("")
        print("  == 运行时报文 ==")
        print("  KIVY_METRICS_DENSITY env = %r" % os.environ.get("KIVY_METRICS_DENSITY"))
        print("  Window.dpi=%.1f  Metrics.density=%.3f  fontscale=%.3f"
              % (getattr(Window, "dpi", -1), Metrics.density, Metrics.fontscale))
        print("  Window.size = %s  ( = %.0f x %.0f dp )"
              % (tuple(Window.size), Window.width / dp(1), Window.height / dp(1)))
        print("  dp(1)=%.3f  sp(11)=%.2f  sp(22)=%.2f" % (dp(1), sp(11), sp(22)))
        nodes = walk(self.root, [])
        bar = None
        for wd in nodes:
            if isinstance(wd, BoxLayout) and wd.parent is not None:
                if sum(1 for c in wd.children if isinstance(c, Button)) >= 4:
                    bar = wd
                    break
        if bar is None:
            print("  !! 找不到底栏")
            Clock.schedule_once(lambda d: self.stop(), 0.2)
            return
        print("  底栏 width=%.0f  x=%.0f..%.0f  **height=%.1f  y=%.1f..%.1f**"
              % (bar.width, bar.x, bar.right, bar.height, bar.y, bar.top))
        tot = 0.0
        for c in bar.children:
            tot += c.width
            off = "  **出屏**" if (c.x < -0.5 or c.right > Window.width + 0.5) else ""
            txt = getattr(c, "text", "")
            print("    %-10s x %7.1f..%7.1f  w %6.1f  text=%r%s"
                  % (type(c).__name__, c.x, c.right, c.width, txt,
                     off + ("  h=%.1f y=%.1f..%.1f" % (c.height, c.y, c.top))))
        print("  子控件合计 %.1f / 窗宽 %d (%.1f%%)" % (tot, int(Window.width),
                                                   100.0 * tot / Window.width))
        # 顶部色块行: 每个按钮的**文字自然宽** vs 按钮宽
        row = None
        for wd in nodes:
            if isinstance(wd, BoxLayout) and sum(
                    1 for c in wd.children
                    if isinstance(c, Button) and c.text in
                    ("金沙", "红沙", "蓝沙", "绿沙", "紫沙", "黑沙")) >= 5:
                row = wd
                break
        if row is not None:
            print("  色块行 width=%.0f  **height=%.1f  y=%.1f..%.1f**  子控件高=%s"
                  % (row.width, row.height, row.y, row.top,
                     [round(c.height, 1) for c in row.children[:3]]))
            from kivy.core.text import Label as _CL
            for c in row.children:
                if not getattr(c, "text", ""):
                    continue
                try:
                    cl = _CL(text=c.text, font_size=c.font_size,
                             font_name=c.font_name or "Roboto")
                    cl.refresh()
                    need = cl.content_size[0]
                except Exception:
                    need = -1
                print("    %-6s 按钮宽 %6.1f  文字需 %6.1f  => %s"
                      % (c.text, c.width, need,
                         "**溢出**" if need > c.width else "放得下"))
        Clock.schedule_once(lambda d: self.stop(), 0.2)


if __name__ == "__main__":
    P().run()
