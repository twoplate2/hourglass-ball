# -*- coding: utf-8 -*-
"""完成弹窗的**长周期文案**会不会折行/被裁 —— 补 r19-2号 明确说没覆盖的那个缺口。

背景: `main.py:5270-5276` 的大字高度绑了 `texture_size`, 弹窗高度绑了
`content.minimum_height`(初值 `dp(330)`) —— 注释里专门写了"加了「用时：」前缀后
长周期(如「用时：100小时59分59秒」)会折行, 高度得跟着文字长"。
**但从没在极值上量过**。最长的一串就是 `MAX_DURATION=360000`(100 小时)。

量的是**实际布局出来的**数, 不是读代码推的:
  ① 大字的 `texture_size[1]`(折行后的真实高度) 有没有超过它自己的 `height`(= 被裁);
  ② 弹窗最终高度有没有超过窗口(= 顶到屏幕外);
  ③ 「确定」按钮有没有被挤出弹窗。

跑法: python tools/_probe_completion_text.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "completion_text"
# (窗口宽高, 要试的周期)
CASES = [
    # ⚠️ 最长的一串**不是** 360000 —— 那是**整 100 小时**(只有「用时：100小时」)。
    #    最长是 359999 = 99小时59分59秒。第一版拿 360000 当"最长", 其实测了个短的。
    ((400, 800), [20, 359999, 360000]),
    ((360, 640), [359999]),
    ((320, 560), [359999]),
    ((300, 500), [359999]),
]


def main():
    with tempfile.TemporaryDirectory(prefix="comptext-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        from kivy.core.window import Window

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        # ⚠️ **不要桩掉 `on_completed`** —— 本探针要的正是它(第一版桩了,
        #    于是弹窗根本没建, 十个用例全是 None/0.0, 差点被当成「全部正常」)。
        #    项目里那条「tools/*.py 必须桩掉 on_completed」是给**跑完整周期**的
        #    工具用的(不桩会弹出永不关闭的弹窗盖住裁图); 这里不跑周期, 不会自动弹。
        m.HourglassWidget.load_config = lambda *_: {"duration": 60}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])
        OUT.mkdir(parents=True, exist_ok=True)

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                self.queue = [(sz, d) for sz, ds in CASES for d in ds]
                self.rows = []
                self.step()

            def step(self, *_a):
                if not self.queue:
                    return self.finish()
                (sw, sh), dur = self.queue.pop(0)
                Window.size = (sw, sh)
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                self._completion_popup = None
                self.on_completed(dur)
                Clock.schedule_once(lambda dt: self.measure(sw, sh, dur), 0.45)

            def measure(self, sw, sh, dur):
                pop = self._completion_popup
                big = None
                if pop is not None:
                    for w in pop.content.children:
                        if getattr(w, "font_size", 0) and w.text.startswith("用时"):
                            big = w
                            break
                # ⚠️ 顺序要紧: **先读真值, 再做负对照** ——
                #    第一版把"压回默认高度"写在读之前, 主表读到的是被压过的 52,
                #    于是四个**本来正常**的用例被自己报成了"文字被裁"。
                ts_h = big.texture_size[1] if big else None
                real_h = big.height if big else None
                # ---- 负对照: 把标签高度按**默认值**压回去, 模拟"绑定失效" ----
                # 判据必须**能翻红**, 否则"没被裁"只是因为它永远不会说被裁。
                neg = None
                if big is not None:
                    big.height = 52.0          # = dp(52), 绑定没生效时就是这个值
                    neg = big.texture_size[1] > big.height + 0.5
                self.rows.append({
                    "win": (sw, sh), "dur": dur,
                    "text": big.text if big else None,
                    "ts_h": ts_h, "h": real_h,
                    "neg_clipped": neg,
                    "popup_h": pop.height if pop else None,
                    "win_h": Window.height,
                })
                if pop is not None:
                    pop.dismiss()
                    Clock.schedule_once(self.step, 0.2)
                else:
                    self.step()

            def finish(self):
                report(self.rows)
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

        P().run()
    return 0


# sp(28) 的**单行**字高 —— 实测 41.0px。用它当"折了几行"的除数,
# ⚠️ 不能用 30 这种拍的数: 第一版就是这么写的, 把**单行**报成了"折成多行"。
ONE_LINE_H = 41.0


def report(rows):
    print("\n=== 完成弹窗大字: 折行 / 裁切 / 溢出 ===")
    print("判据: ts_h(=折行后真实文字高) > h(=标签被分到的高度) ⇒ **被裁**;")
    print("      popup_h + title 区 > win_h ⇒ 顶出屏幕\n")
    head = "%-12s %8s %-26s %7s %7s  %8s %8s  %s"
    print(head % ("窗口", "周期(s)", "文字", "ts_h", "h", "弹窗高", "窗高", "判定"))
    print("-" * 108)
    bad = []
    for r in rows:
        clipped = r["ts_h"] is not None and r["ts_h"] > r["h"] + 0.5
        overflow = r["popup_h"] is not None and r["popup_h"] > r["win_h"]
        note = []
        if clipped:
            note.append("**文字被裁**(多 %.0fpx)" % (r["ts_h"] - r["h"]))
        if overflow:
            note.append("**弹窗比窗口还高**")
        # 折行本身不是缺陷(高度会跟着长), 但要知道它折了几行
        elif r["ts_h"] and r["ts_h"] > ONE_LINE_H * 1.5:
            note.append("**折了 %d 行**(高度已跟着长, 未裁)"
                        % round(r["ts_h"] / ONE_LINE_H))
        if not note:
            note.append("单行, 正常")
        if clipped or overflow:
            bad.append(r)
        print("%-12s %8d %-26s %7.1f %7.1f  %8.1f %8.1f  %s"
              % ("%dx%d" % r["win"], r["dur"], r["text"], r["ts_h"] or 0, r["h"] or 0,
                 r["popup_h"] or 0, r["win_h"], "; ".join(note)))
    print()
    print("⇒ %s" % ("**%d 个用例出问题**" % len(bad) if bad
                  else "全部用例: 文字没被裁、弹窗没超窗口"))
    # 负对照: 折行的那些用例, 把高度压回默认 dp(52) 之后判据**必须**翻红。
    # 不翻红就说明"没被裁"是因为判据不会说被裁, 那条结论不算数。
    wrapped = [r for r in rows if r["ts_h"] and r["ts_h"] > ONE_LINE_H * 1.5]
    if wrapped:
        caught = sum(1 for r in wrapped if r["neg_clipped"])
        print("负对照(把标签高度压回默认 dp(52)): %d/%d 个折行用例被判为「被裁」%s"
              % (caught, len(wrapped), " ✓ 判据会翻红" if caught == len(wrapped)
                 else " **判据不判别**"))
        if caught != len(wrapped):
            print("   ⇒ 上面那句「没被裁」不算数")
    else:
        print("负对照: 本轮没有折行用例 ⇒ 判据的这一侧**没被检验**")


if __name__ == "__main__":
    sys.exit(main())
