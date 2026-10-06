# -*- coding: utf-8 -*-
"""**几何缓存必须随几何代失效** —— `_upper_cols` 的守卫(2026-10-07 2号专家查出)。

## 这条守的是什么

`_upper_area`(`main.py` 牛顿迭代内核)里有一份 `_upper_cols`:**每条列**的
`(dx, floor, roof)`(含一个 `sqrt`)。它**只跟几何有关**, 却带着"建过一次就够"的守卫:

    if cols is None or len(cols) != n + 1:      # n = MOUND_SHAPE_NODES-1 是**常量**
        ...重建...

⇒ **建过一次永不重建**。`_reset_run_state` 里清过它, 但 **转屏 / 分屏 / 拖窗**走的是
`_on_size → _rebuild_height_table` —— 那条路上它**没被清**(而同一处的 `_upper_env_h`
却清了)。后果: 上球沙面的 `level` 一直用**旧 `Ri`** 的几何算。

## 判据(自校验, 不需要"改前"那一版)

**几何重建之后, 清掉 `_upper_cols` 再算一遍, 结果必须一模一样。**
- 若缓存确实随几何失效 ⇒ 清与不清是同一个东西 ⇒ 差 0。
- 若缓存陈旧 ⇒ 清掉会让它按新几何重建 ⇒ 结果变 ⇒ **差非 0, 翻红**。

⚠️ 这条判据**自带负对照**: 把 `_rebuild_height_table` 里那行 `self._upper_cols = None`
删掉, 它必须翻红(实测差 **0.3153px**)。分不开正负的控制等于没有控制。

跑法: python tools/_probe_upper_cols_gen.py
退出码 0 = 通过。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SIZES = ((400, 800), (720, 900), (300, 640))


def run():
    with tempfile.TemporaryDirectory(prefix="upcols-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App                            # noqa: F401
        from kivy.clock import Clock
        from kivy.core.window import Window
        import main as module

        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": 15.0}
        module.HourglassWidget.save_config = lambda *_: None

        box = {"bad": 0, "rows": []}

        class ProbeApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.go, 1.0)

            def go(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                w.completion_enabled = False
                w.set_duration(15.0)
                # ⚠️ **第一个尺寸才 reset** —— `_reset_run_state` 里也有一句
                #    `self._upper_cols = None`, 每个尺寸都 reset 就等于"每次都替被测对象
                #    擦干净了缓存", 这条判据会**永远通过**。而真实故障路径
                #    (`_on_size → _rebuild_height_table`)**不 reset**。第一版就是这么废掉的。
                for i, (sx, sy) in enumerate(SIZES):
                    # ⚠️ **直接改 widget 的 `size`** —— 第一版写的是 `Window.size = ...`,
                    #    但 widget 在布局里, 它的 size 由布局决定 ⇒ 三个尺寸下 `Ri` **完全没变**
                    #    (全是 139.4156) ⇒ 几何从没换过 ⇒ 这条判据**根本没测到被测对象**
                    #    (项目文档里点名的"判据自己把被测对象排除了")。下面还加了
                    #    "Ri 必须真的随尺寸变"的断言, 免得它再退化成空转。
                    w.size = (sx, sy)
                    w._rebuild_height_table()          # 与 `_on_size` 同一条路(**不 reset**)
                    if i == 0:
                        w.reset()
                        w.toggle()
                        for _ in range(30):            # 让沙面离开极值, 别落在退化分支上
                            w.elapsed += 1 / 60.0
                            w.tick(1 / 60.0)
                    w.elapsed = w.duration * 0.45
                    h = w._upper_sand_height_px()
                    # ⚠️ `_upper_level_for` **自己还有一层每帧 memo** —— 只清 `_upper_cols`
                    #    不清它, 第二次会直接返回上一次的结果 ⇒ 差恒为 0(第一版就栽在这)。
                    w._upper_level_key = None
                    got = w._upper_level_for(h)        # 用**当前那份**(可能陈旧的)几何缓存
                    w._upper_cols = None               # ★ 判据: 逼它按当前几何重建
                    w._upper_level_key = None
                    again = w._upper_level_for(h)
                    d = abs(got - again)
                    box["rows"].append((sx, sy, w._R_inner, got, again, d))
                    if d != 0.0:
                        box["bad"] += 1
                self.stop()

        ProbeApp().run()
        print("  %-12s %-10s %-14s %-14s %s" % ("窗口", "Ri", "缓存值", "清后重算", "差"))
        for sx, sy, ri, a, b, d in box["rows"]:
            print("  %-12s %-10.4f %-14.6f %-14.6f %.4f%s"
                  % ("%dx%d" % (sx, sy), ri, a, b, d, "  ← 翻红" if d else ""))
        ris = {round(r[2], 6) for r in box["rows"]}
        if len(ris) < 2:
            print("!! **判据无效**: %d 个尺寸下 `Ri` 全是 %s —— 几何根本没换过, "
                  "这条测试等于什么都没测" % (len(box["rows"]), ris))
            return 2
        if box["bad"]:
            print("!! %d/%d 个尺寸上『清掉几何缓存』改变了结果 ⇒ `_upper_cols` **没有随几何失效**"
                  % (box["bad"], len(box["rows"])))
            return 1
        print("==> 通过: 每个尺寸上清与不清结果相同(= 缓存确实随几何代失效)")
        return 0


sys.exit(run())
