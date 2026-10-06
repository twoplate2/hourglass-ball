# -*- coding: utf-8 -*-
"""`LandLayer._popups` 登记表会不会**攒下已销毁的弹窗**(1.144 引入的隐患, 自测)。

动机: 我在 r34-1号 的简报里点了这条"我很想被证伪" —— 与其等别人查, 自己先量。
风险形态: `track_popup` 只在 `open()` 里调, 解登记只在 `_real_remove_widget()` 里调。
若某条 dismiss 路径**不经过** `_real_remove_widget`, 集合就会永久持有那个弹窗
(强引用 ⇒ 整个控件树泄漏), 而且下次转屏 `apply_orientation` 会去 `rehost` 一个死对象
(那里有 try/except 吞掉 —— **吞掉不等于没问题**)。

判据(先标定): 集合大小必须**回到开窗前的值**。标定用"开一次+立刻读"确认能涨到 1
(如果开了也不涨, 说明我量错了对象, 不是"没泄漏")。

跑法: python tools/_probe_popup_leak.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    # ⚠️ Kivy 的参数解析器在**遇到第一个非 "-" 开头的参数就停**。若 argv 里只有
    #    `--landscape`, 它会当成自己的未知选项 -> 打印 usage 并退出。
    #    所以先塞一个占位位置参数, 再放 --landscape(main.py 里是 `in sys.argv` 判的, 顺序无关)。
    sys.argv = [sys.argv[0], "_leak", "--landscape"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 50}
    m.HourglassWidget.save_config = lambda *_: None

    log = []
    obsv = []

    def count_obsv(layer, tag):
        """绑定计数: `_unmount` 若漏解 `on_resize`, 每开一次关一次就多留一个观察者
        (BACK 那条回归测的是 `on_keyboard`, **`on_resize` 没被它覆盖**)。"""
        from kivy.core.window import Window as _W
        try:
            nw = len(_W.get_property_observers("on_resize"))
        except Exception:
            nw = -1
        try:
            nl = len(layer.get_property_observers("on_resize"))
        except Exception as _e:
            nl = -1          # ⚠️ 读不到就是读不到, 别假装 0(TODO: 查清原因)
        try:
            nk = len(_W.get_property_observers("on_keyboard"))
        except Exception:
            nk = -1
        obsv.append((tag, nw, nl, nk))

    class P(m.HourglassApp):
        def on_start(self):
            Clock.schedule_once(self.kickoff, 1.2)

        # ⚠️ 别叫 run —— 会**覆盖 `App.run()`**(踩过: TypeError missing '_dt')
        def kickoff(self, _dt):
            layer = m._land_layer()
            Window.size = (1080, 2400)          # 竖屏起步
            Clock.schedule_once(lambda dt: self.rounds(layer), 1.0)

        def rounds(self, layer):
            log.append(("初始", len(layer._popups)))
            count_obsv(layer, "初始")

            def open_and_read(tag, opener, then=None):
                opener()
                # 等 open 完成(竖屏走动画)
                Clock.schedule_once(lambda dt: self.after_open(tag, layer, then), 0.6)

            def step_dismiss(tag, layer):
                # 逐个把当前开着的关掉, 看集合掉不掉
                popped = list(layer._popups)
                n_before = len(popped)
                for p in popped:
                    p.dismiss()
                Clock.schedule_once(
                    lambda dt: self.check(tag, layer, n_before), 0.8)

            self._seq = [
                ("① 开周期弹窗", lambda: self.on_duration_picker(None)),
                ("② 开音效弹窗", lambda: self.on_sound_picker()),
                ("③ 开开发者菜单", lambda: self._open_dev_menu()),
            ]
            self._i = 0
            self._next(layer)

        def _next(self, layer):
            if self._i >= len(self._seq):
                self.final(layer)
                return
            tag, fn = self._seq[self._i]
            self._i += 1
            self._cur_tag = tag
            fn()
            Clock.schedule_once(lambda dt: self.after_open(layer), 0.7)

        def after_open(self, layer):
            log.append(("  %s 后" % self._cur_tag, len(layer._popups)))
            # ⚠️ **必须在"开着的时候"也采一次** —— 只在"关掉之后"采样的话,
            #    那时本来就该回到基线; 若绑定**根本没建立**, 我会读到同样的 0
            #    并宣布"没漏解"。**不判别的判据 = 没有判据**(QA_RULES 二节 1)。
            count_obsv(layer, "  %s 后" % self._cur_tag)
            popped = list(layer._popups)
            for p in popped:
                p.dismiss()
            Clock.schedule_once(lambda dt: self.after_dismiss(layer, len(popped)), 0.9)

        def after_dismiss(self, layer, n):
            log.append(("  关闭后", len(layer._popups)))
            count_obsv(layer, "  关闭后")
            if len(layer._popups) != 0:
                log.append(("**泄漏! 残留 %d 个**" % len(layer._popups), -1))
            self._next(layer)

        def final(self, layer):
            # 再连开连关 5 轮, 看会不会累积
            for _ in range(5):
                self.on_duration_picker(None)
            Clock.schedule_once(self.after_burst, 1.0)

        def after_burst(self, _dt):
            layer = m._land_layer()
            log.append(("连开 5 次后", len(layer._popups)))
            for p in list(layer._popups):
                p.dismiss()
            Clock.schedule_once(self.done, 1.0)

        def done(self, _dt):
            layer = m._land_layer()
            log.append(("连关后", len(layer._popups)))
            count_obsv(layer, "连关后")
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    return log, obsv


if __name__ == "__main__":
    rows, obsv = main()
    print("")
    print("  === LandLayer._popups 登记表 ===")
    for tag, n in rows:
        print("  %-22s %s" % (tag, n if n >= 0 else n))
    # ⚠️ 判据只能看**该为 0 的那些行**("关闭后"/"连关后")。
    #    第一版写成 `r[1] != 0 and 不是初始 and 不是连开5次` —— 把"开后本来就是 1"那三行也算成泄漏,
    #    于是**数据全对却报了"有泄漏"**(判据没把"期望为 1"和"泄漏"分开, QA_RULES §七 同一种病)。
    closing = [r for r in rows if r[0].startswith("  关闭后") or r[0] == "连关后"]
    bad = [r for r in closing if r[1] != 0]
    print("")
    print("  该为 0 的行: %d 行(必须 >0, 否则是**判据没跑到**而不是没泄漏)" % len(closing))
    for r in closing:
        print("    %-22s %s" % (r[0], r[1]))
    if not closing:
        print("  结论: **判据一行都没跑到 —— 本结果无效**")
    else:
        print("  结论: %s" % ("**有泄漏: %s**" % bad if bad
                              else "关闭后一律回到 0 —— 未见泄漏"))
    print("")
    print("  === 绑定计数 (Window.on_resize / layer.on_resize / Window.on_keyboard) ===")
    for tag, nw, nl, nk in obsv:
        print("  %-22s %4d %4d %4d" % (tag, nw, nl, nk))
    if obsv:
        b, e = tuple(obsv[0][1:]), tuple(obsv[-1][1:])
        print("  基线 %s -> 收尾 %s  =>  %s"
              % (b, e, "**绑定数没回到基线 —— 有漏解**" if b != e else "回到基线, 未见漏解"))
