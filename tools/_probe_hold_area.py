# -*- coding: utf-8 -*-
"""**长按版本号**的命中区到底有多大 / 会不会抢到旁边的按钮(2026-10-07)。

用户要求:「把长按版本号出窗口的响应区域**大幅提高**, 至少增加 50% 的长度和宽度」。
改法是**只放 `collide_point`, 不动布局尺寸**(撑大 `size` 会把四个按钮挤走)。

这个探针不靠肉眼, 用**合成触摸**(`kivy.tests.common.UnitTestTouch`)走 Kivy 真正的
派发链(`root.dispatch('on_touch_down', touch)`), 逐点问三件事:

1. 命中区**实际**是多大(放大后 / 可见) —— 必须 ≥ 1.5× 且**上沿真的往上涨**;
2. 落在放大区里的点, **握到的是 HoldArea 自己**(而不是画布/别的);
3. 落在相邻按钮上的点, **按钮照样收到**(不能把按钮边缘变成死区) —— 这是**负对照**:
   把 `HOLD_HIT_W` 调到很大会翻红。

跑法: python tools/_probe_hold_area.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "2.0")
    sys.argv = sys.argv[:1] + ["_ha"]
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.metrics import dp
    from kivy.tests.common import UnitTestTouch
    import main as m

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 60.0}
    m.HourglassWidget.save_config = lambda *_: None

    box = {}

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            hg = self.hourglass
            area = self._benchmark_area
            btns = self._bottom_btns
            x0, y0, x1, y1 = area._hit_rect()
            print("")
            print("  === 长按区几何(逻辑坐标) ===")
            print("  可见矩形 : x %.1f..%.1f  y %.1f..%.1f   = %.1f x %.1f"
                  % (area.x, area.right, area.y, area.top, area.width, area.height))
            print("  命中矩形 : x %.1f..%.1f  y %.1f..%.1f   = %.1f x %.1f"
                  % (x0, x1, y0, y1, x1 - x0, y1 - y0))
            print("  倍数     : 宽 %.2fx   高 %.2fx"
                  % ((x1 - x0) / max(1e-6, area.width), (y1 - y0) / max(1e-6, area.height)))
            for b in btns:
                print("  兄弟按钮 : %-14s x %.1f..%.1f y %.1f..%.1f"
                      % (b.text, b.x, b.right, b.y, b.top))

            # ---- 逐点派发: **调真处理器**, 只记录谁返回了 True(消费了这一下) ----
            def dispatch(x, y):
                got = []
                saved = []
                for w, name in [(area, "hold_area")] + [(b, b.text) for b in btns]:
                    _orig = w.on_touch_down          # ⚠️ 先抓住原方法, 否则包装后自递归
                    saved.append((w, _orig))

                    def h(t, _f=_orig, _n=name):
                        r = _f(t)
                        if r:
                            got.append(_n)
                        return r
                    w.on_touch_down = h
                try:
                    t = UnitTestTouch(x, y)
                    # ⚠️ `UnitTestTouch(x, y)` 构造出来的 `pos/x/y` **全是 0**(构造函数那时
                    #    窗口还没定尺寸)。必须走 `depack(归一化)` —— 它一次把 **sx/sy 和 x/y**
                    #    都设对。**只赋 `pos` 不够**: `ButtonBehavior` 查的是 `touch.x/touch.y`,
                    #    只赋 pos 会让按钮"看不见这一下", 量出来像"按钮没被抢"。
                    t.pos = (float(x), float(y))
                    t.x, t.y = float(x), float(y)
                    self.root.dispatch("on_touch_down", t)
                    self.root.dispatch("on_touch_up", t)
                    area._cancel_hold()          # 别让 3 秒后的定时器留下
                    area._touch = None
                finally:
                    for w, d in saved:
                        w.on_touch_down = d
                return got[0] if got else "(没人接)"

            cx, cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
            # ⚠️ 派发用的是**窗口坐标**, 而 `x/y/width` 是**父坐标** —— LandLayer 里两者差一个
            #    锚点偏移(实测差几十 px)。**必须 `to_window()` 换算**, 否则量出来"谁都没命中",
            #    看起来像"改动生效了"(这正是这个探针第一版骗我的地方)。
            probes = [
                ("可见区中心(版本文字上)", area, area.center_x, area.center_y),
                ("命中区最上沿内侧", area, cx, y1 - 2),
                ("命中区左缘内侧", area, x0 + 2, cy),
                ("命中区右缘内侧", area, x1 - 2, cy),
                ("← 音效按钮中心(左邻居)", self.sound_btn,
                 self.sound_btn.center_x, self.sound_btn.center_y),
                ("← 音效按钮靠右缘 2px", self.sound_btn,
                 self.sound_btn.right - 2, self.sound_btn.center_y),
                ("周期按钮中心(左邻居)", self.duration_btn,
                 self.duration_btn.center_x, self.duration_btn.center_y),
                ("开始按钮靠左缘 2px(右邻居)", self.start_btn,
                 self.start_btn.x + 2, self.start_btn.center_y),
                ("命中区外一点点(左边)", area, x0 - dp(8), cy),
            ]
            print("")
            print("  === 命中的是谁 ===")
            for label, w, px, py in probes:
                wx, wy = w.to_window(px, py)
                print("    %-30s 父(%.1f, %.1f) 窗(%.1f, %.1f) -> %s"
                      % (label, px, py, wx, wy, dispatch(wx, wy)))

            # ---- 代价: 新长出来的那一条盖住了画布, 里面有多少是"点沙漏会开始/暂停"的热区 ----
            # (画布的 `on_touch_down` 只在**玻璃轮廓内**才 toggle —— 两球圆内 或 颈部宽带内)
            hg = self.hourglass
            y_top = y1
            y_bot = area.top
            inside = total = 0
            yy = y_bot
            while yy <= y_top:                        # 逐行扫, 步长 2px
                xx = x0
                while xx <= x1:
                    total += 1
                    dx = xx - hg._cx
                    in_ball = (dx * dx + (yy - hg._lower_y_c) ** 2 <= hg._R ** 2 or
                               dx * dx + (yy - hg._upper_y_c) ** 2 <= hg._R ** 2)
                    if in_ball:
                        inside += 1
                    xx += 2
                yy += 2
            pct = 100.0 * inside / max(1, total)
            print("")
            print("  === 新长出来那一条盖住了多少\"点沙漏开始/暂停\"的热区 ===")
            print("  该条 y %.1f..%.1f, x %.1f..%.1f (画布逻辑 y 0..%.1f)"
                  % (y_bot, y_top, x0, x1, hg.height))
            print("  落在两球轮廓内的采样点: %d/%d = **%.1f%%**" % (inside, total, pct))
            if pct > 0:
                print("  ⇒ 这一条里 %.1f%% 的地方原来点一下会开始/暂停, 现在会被长按区吃掉" % pct)
            else:
                print("  ⇒ 该条全在沙漏轮廓外, **零代价**")

            # ---- 行为对照: 在**新长出来那一条**里, 短按 vs 长按 ----
            # 短按必须**还给画布**(那块地方原来点一下是开始/暂停), 长按必须**进开发者菜单**。
            print("")
            print("   === 新长出来那一条: 短按 vs 长按 ===")
            # 取一个**真的在两球轮廓内**的点(否则"没触发开始"是理所当然的, 验不到透传):
            # y 取这一条的上沿、x 取球心。
            py_ = y1 - 2
            px_ = hg._cx
            dxb = px_ - hg._cx
            on_ball = (dxb * dxb + (py_ - hg._lower_y_c) ** 2 <= hg._R ** 2 or
                       dxb * dxb + (py_ - hg._upper_y_c) ** 2 <= hg._R ** 2)
            print("   取点 (%.1f, %.1f): 在下球轮廓内 = %s  (球心 y=%.1f R=%.1f)"
                  % (px_, py_, on_ball, hg._lower_y_c, hg._R))

            def one_case(bx, by, hold):
                fired, toggles, fwd = [], [], []
                orig_act, orig_fwd = area._activate, area._forward_tap
                area._activate = lambda *a: fired.append(True)
                area._forward_tap = lambda tch: (fwd.append(True), orig_fwd(tch))[1]
                orig_toggle = self.on_toggle
                self.on_toggle = lambda *a: toggles.append(True)
                tt = UnitTestTouch(bx, by)
                tt.pos = (float(bx), float(by))
                tt.x, tt.y = float(bx), float(by)
                self.root.dispatch("on_touch_down", tt)
                claimed = area._touch is not None
                # ⚠️ `touch.grab_current` 是**事件循环**在派发时维护的 —— 直接
                #    `root.dispatch()` 绕过了它, 于是 `on_touch_up` 里
                #    `if touch.grab_current is self` 不成立 ⇒ `_touch` 永远清不掉
                #    ⇒ **下一个用例一上手就被"上一次的触摸还按着"挡掉**。
                #    (第一版就是栽在这: 长按那格量出"既开了菜单又触发了开始"。)
                #    这里手动补上事件循环那一句, 让探针走的是真实状态机。
                if claimed:
                    tt.grab_current = area
                if hold:
                    area._held(0)
                self.root.dispatch("on_touch_up", tt)
                area._cancel_hold()
                area._activate, area._forward_tap = orig_act, orig_fwd
                self.on_toggle = orig_toggle
                self.hourglass.running = False
                return claimed, bool(fired), bool(toggles), bool(fwd)

            wx, wy = area.to_window(px_, py_)
            c1, m1, t1, f1 = one_case(wx, wy, hold=False)
            c2, m2, t2, f2 = one_case(wx, wy, hold=True)
            print("    短按: 握到=%s 菜单=%s 开始/暂停=%s 透传=%s   (期望 握到/无菜单/触发/透传)"
                  % (c1, m1, t1, f1))
            print("    长按: 握到=%s 菜单=%s 开始/暂停=%s 透传=%s   (期望 握到/进菜单/不触发/不透传)"
                  % (c2, m2, t2, f2))
            bax, bay = area.to_window(area.center_x, area.center_y)
            c3, m3, t3, f3 = one_case(bax, bay, hold=True)
            print("    版本文字上长按: 握到=%s 菜单=%s 开始/暂停=%s  (期望 握到/进菜单/不触发)"
                  % (c3, m3, t3))
            box["beh"] = c1 and (not m1) and t1 and f1 and c2 and m2 and (not t2) and (not f2) and m3

            # ---- 结论 ----
            hit = area._hit_rect()
            w_ok = (hit[2] - hit[0]) >= area.width * 1.5 - 1e-6
            h_ok = (hit[3] - hit[1]) >= area.height * 1.5 - 1e-6
            up_ok = hit[3] > area.top + area.height * 0.2      # 上沿真的往上长了
            print("")
            print("  放大 ≥1.5×宽 : %s" % ("OK" if w_ok else "!! 不够"))
            print("  放大 ≥1.5×高 : %s" % ("OK" if h_ok else "!! 不够"))
            print("  上沿向上长    : %s (+%.1f)"
                  % ("OK" if up_ok else "!! 没长", hit[3] - area.top))
            print("  行为(短按透传/长按进菜单): %s" % ("OK" if box.get("beh") else "!! 不对"))
            box["ok"] = w_ok and h_ok and up_ok and box.get("beh")
            self.stop()

    P().run()
    return 0 if box.get("ok") else 1


sys.exit(run())
