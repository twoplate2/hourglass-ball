# -*- coding: utf-8 -*-
"""底栏在不同 **density** 下的布局 —— 版本号会不会被挤折行, 按钮会不会撑爆底栏。

起因(r28-2号 2026-10-06 设备实测): 设备 B(density **480**)上主界面版本号被折成**三行**
`v1.` / `13` / `4`; 设备 A(density 280)上同一份代码**一行好好的**。
两台屏宽都是 1080 ⇒ 差的是 `dp()` 倍率(480/160 = 3.0 vs 280/160 = 1.75)。
`dp(82) + dp(74)*3` 在 density 480 上要占 912/1080, 只剩 ~85px 给版本号。

**实测结论（六档全跑过）**: 按钮合计**恒为 304 dp**(532/1.75 == 912/3 == 1216/4),
与密度无关 ⇒ **真正决定成败的唯一变量是"窗口有多少 dp 宽"**。
**density 480 + 1080 宽 = 360 dp** 是极常见的真机配置, 此时版本区 48 px、版本文字需 48 px
—— **恰好在折行门槛上**(与 r28-2号 的设备读数对上)。

🔴 **别再拿"固定屏宽 1080、密度扫到 560/640"当"高密度会不会挤爆"** ——
`1080 ÷ 640/160 = 270 dp`, **而 Android 支持的最窄全屏宽度是 320 dp**;
270/309 dp 的窗口**只可能来自分屏 / 自由窗口**。那个组合**不是真机**,
"640 档 113% 撑爆"是**喂错输入喂出来的**(QA_RULES §七第 5 问)。
本探针现在会把每行的 `屏宽(dp)` 打出来, 并对 < 320 dp 的行自动标注"非全屏宽度"。

⚠️ **v1 有三个 bug, 全部在 2026-10-06 修掉 —— 修之前它六个 case 全报异常, 零数据**
（而它当时已经被提交进仓库了。**"提交了但没跑过" = 假工具**）:
  1. 没 `sys.path.insert(0, ROOT)` ⇒ `ModuleNotFoundError: No module named 'main'`;
  2. 六个 case 挤在**一个进程**里反复 `del sys.modules['main']` 再 import —— Kivy 是单例,
     同进程连开多个 App 会互相污染(弹窗探针 v1 就因此崩过)。**改成一进程一例**;
  3. 🔴 **`Window.size` 设在 `app.build()` 之前, 会被 build() 覆盖** ——
     `HourglassApp.build()` 里写死 `if "--landscape" not in argv: Window.size = (400, 800)`
     ⇒ 探针扫 1080x1920 其实跑在 **400x800** 上。
     **这正是 `QA_RULES.md` §七第 6 问("我喂进去的输入, 真的喂进去了吗?")的字面例子**,
     写完那条规则的当天又抓到一例。现在**设完回读, 不等就报 MISMATCH**。

跑法(一进程一例):
  python tools/_probe_density_layout.py <density> <宽> <高>
  # 或扫全套:
  for d in 280 320 440 480 560 640; do python tools/_probe_density_layout.py $d 1080 1920; done
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASES = ((280, 1080, 2400), (320, 1080, 1920), (440, 1080, 2340),
         (480, 1080, 1920), (560, 1080, 1920), (640, 1080, 1920))


def density_scale():
    from kivy.metrics import dp
    return dp(1)


def measure_bar(root, w):
    from kivy.uix.button import Button
    from kivy.uix.boxlayout import BoxLayout

    def walk(wd, out, d=0):
        out.append(wd)
        if d > 8:
            return out
        for c in getattr(wd, "children", []):
            walk(c, out, d + 1)
        return out

    bar = None
    for wd in walk(root, []):
        if isinstance(wd, BoxLayout) and wd.parent is not None:
            if sum(1 for c in wd.children if isinstance(c, Button)) >= 4:
                bar = wd
                break
    if bar is None:
        return "找不到底栏"
    # app 已跑满 1.2s, 布局早就做完了; 不再手动 do_layout
    # (v1 在这里对 Button 调 do_layout —— Button 不是 Layout, 没有这个方法)
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
    dpw = w / (density_scale() or 1.0)
    # ⚠️ QA_RULES §七第 5 问: 我喂给它的输入, 是应用真的会产生的那种吗?
    #    按钮恒需 304 dp(实测 532/1.75 == 1216/4), 所以真正决定成败的是**窗口有多少 dp 宽**。
    #    Android 支持的最窄全屏宽度是 320 dp ⇒ 低于它的行只可能来自分屏/自由窗口, 单独标注。
    note = "" if dpw >= 319 else "  [非全屏宽度(%.0f dp < 320), 只见于分屏/自由窗口]" % dpw
    return ("按钮合计 %.0f (=%.0f dp, 与密度无关) / 屏宽 %d (%.0f%%) | 屏宽=%.0f dp | "
            "版本区 %.0f px | 版本文字需 %.0f px => %s%s"
            % (bw, bw / (density_scale() or 1.0), w, 100.0 * bw / w, dpw, aw, need,
               "**折行/放不下**" if need > aw else "放得下", note))


def run_case(density, w, h):
    os.environ["KIVY_METRICS_DENSITY"] = str(density / 160.0)
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    # 不碰主机配置(项目红线): 配置读写与音效全桩掉
    m.HourglassWidget.load_config = lambda *_: {"duration": 50}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None

    res = {}

    class P(m.HourglassApp):
        def on_start(self):
            # ⚠️ 无事件循环时 Window.size 赋值不落地(实测停在 config 的 800x600),
            #    必须等窗口真的建起来再设 —— 见本文件头部 bug 3。
            try:
                Window.size = (w, h)
            except Exception:
                pass
            Clock.schedule_once(self.measure, 1.2)

        def measure(self, _dt):
            got = (int(Window.size[0]), int(Window.size[1]))
            if got != (w, h):
                res["r"] = ("**MISMATCH 要 %dx%d 实得 %dx%d —— 本行结论无效**"
                            % (w, h, got[0], got[1]))
            else:
                try:
                    res["r"] = measure_bar(self.root, w)
                except Exception as exc:
                    res["r"] = "测量异常: %r" % (exc,)
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    return res.get("r", "没跑起来")


def main():
    if len(sys.argv) >= 4:
        density, w, h = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    else:
        density, w, h = CASES[0]
    try:
        r = run_case(density, w, h)
    except Exception as exc:
        r = "异常: %r" % (exc,)
    print("  %-7d %-10s %-7.2f | %s" % (density, "%dx%d" % (w, h), density / 160.0, r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
