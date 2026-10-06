# -*- coding: utf-8 -*-
"""底栏在不同 **density** 下的布局 —— 版本号会不会被挤折行, 按钮会不会撑爆底栏。

起因(r28-2号 2026-10-06 设备实测): 设备 B(density **480**)上主界面版本号被折成**三行**
`v1.` / `13` / `4`; 设备 A(density 280)上同一份代码**一行好好的**。
两台屏宽都是 1080 ⇒ 差的是 `dp()` 倍率(480/160 = 3.0 vs 280/160 = 1.75)。

`dp(82) + dp(74)*3` 在 density 480 上要占 912/1080, 只剩 ~85px 给版本号。
⇒ **1080p + 480dpi 是极常见的真机配置**, 所以要问的不只是"版本号折行",
还有"**再高一点会不会把按钮挤出屏幕**"。

本探针在**固定屏宽**下扫 density, 直接量底栏各控件的实际宽度与版本号可用宽度。
跑法: python tools/_probe_density_layout.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# (density, 屏宽, 屏高) —— 覆盖真机常见档 + 边界
CASES = ((280, 1080, 2400), (320, 1080, 1920), (440, 1080, 2340),
         (480, 1080, 1920), (560, 1080, 1920), (640, 1080, 1920))


def run_case(density, w, h):
    os.environ["KIVY_METRICS_DENSITY"] = str(density / 160.0)
    for m in [k for k in list(sys.modules) if k in ("main", "kivy.metrics")]:
        del sys.modules[m]
    import main as m
    from kivy.core.window import Window
    Window.size = (w, h)
    from kivy.metrics import dp
    app = m.HourglassApp()
    app.root = None

    class T:
        pass
    t = T()
    try:
        app.build()
    except Exception as exc:
        return "build 失败: %r" % (exc,)
    bottom = None
    for ch in app.root.children if hasattr(app.root, "children") else []:
        pass
    # 直接从 app 上取(源码里是局部变量, 所以改用 widget 树找)
    def walk(wd, out):
        out.append(wd)
        for c in getattr(wd, "children", []):
            walk(c, out)
        return out
    widgets = walk(app.root, [])
    from kivy.uix.button import Button
    from kivy.uix.boxlayout import BoxLayout
    bar = None
    for wd in widgets:
        if isinstance(wd, BoxLayout) and wd.parent is not None:
            kinds = [type(c).__name__ for c in wd.children]
            if sum(1 for c in wd.children if isinstance(c, Button)) >= 4:
                bar = wd
                break
    if bar is None:
        return "找不到底栏"
    from kivy.clock import Clock
    bar.do_layout()
    for c in bar.children:
        c.do_layout()
    btns = [c for c in bar.children if isinstance(c, Button)]
    area = [c for c in bar.children if not isinstance(c, Button)]
    bw = sum(c.width for c in btns)
    aw = area[0].width if area else 0.0
    lab = None
    for a in area:
        for c in getattr(a, "children", []):
            if hasattr(c, "text") and str(c.text).startswith("v"):
                lab = c
    need = 0.0
    if lab is not None:
        try:
            lab.texture_update()
            need = lab.texture_size[0]
        except Exception:
            need = -1
    return ("按钮合计 %.0f / 屏宽 %d (%.0f%%) | 版本区 %.0f px | 版本文字需 %.0f px ⇒ %s"
            % (bw, w, 100.0 * bw / w, aw, need,
               "**折行/放不下**" if need > aw else "放得下"))


def main():
    print("")
    print("  density  屏         dp倍率 | 布局实测")
    print("  " + "-" * 78)
    for density, w, h in CASES:
        try:
            r = run_case(density, w, h)
        except Exception as exc:
            r = "异常: %r" % (exc,)
        print("  %-7d %-10s %-7.2f | %s" % (density, "%dx%d" % (w, h), density / 160.0, r))
    print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
