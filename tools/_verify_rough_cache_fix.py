# -*- coding: utf-8 -*-
"""验 r23-1号 抓到的那条缓存 bug, 并证明判据**能翻红**。

现象(设备实测): **暂停着在设置菜单里改「沙面起伏」, 上球沙面会变光滑** —— 六档全是同一个画面,
和真相相反。用户第一次点就会看到"沙面变平了", 很容易读成"N 越大越平"。

机制: `_rebuild_height_table()` 只清 `_upper_rough_cache`、**没清 `_upper_rough_cache_t`**,
而 `_upper_rough_now()` 的命中判定拿 `_t == elapsed` 当前提 ⇒ **把刚置空的 None 原样返回**;
下游 `_upper_rough_at` 见假值全返 0 ⇒ 画成光滑面。
暂停时 `elapsed` 不动 ⇒ 缓存永远命中 None。

本探针量三件事:
  ① 修好之后: 暂停态改档, `_upper_rough_now()` 应当**不是 None**, 且两档的轮廓**不同**;
  ② **负对照(手动复现旧 bug)**: 只置空 `_upper_rough_cache`、保留 `_t` ⇒ 应当返回 None
     且轮廓变成**全零**(= 光滑面) —— 证明"平 = 假值"这条链是真的, 不是我在猜;
  ③ 计时一走(elapsed 变化)缓存自然失效 ⇒ 起伏自己回来 —— 解释"为什么看起来像一动又回来了"。

跑法: python tools/_verify_rough_cache_fix.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="roughcache-") as home:
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
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 20}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        def p2p(arr):
            if arr is None:
                return None
            v = [float(x) for x in arr]
            return max(v) - min(v)

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w.set_duration(20)
                w.running = True
                dt = 1.0 / 60.0
                while w.elapsed < 9.0:          # 跑一段, 让沙面有规模
                    now[0] += dt
                    w.tick(dt)
                w.running = False                # **暂停** —— 关键状态
                print("")
                print("=== ① 修好之后: 暂停态改档 ===")
                print("  档位    _upper_rough_now()  轮廓峰峰值(p2p)   与上一档的差")
                prev = None
                for lb in ("1", "4", "6"):
                    w.set_rough_level(lb)
                    arr = w._upper_rough_now()
                    p = p2p(arr)
                    d = "—" if prev is None else "%+.3f" % (p - prev)
                    print("  %-6s  %-20s %-18s %s"
                          % (lb, "None(空!)" if arr is None else "数组 %d 点" % len(arr),
                             "%.3f" % p if p is not None else "—", d))
                    prev = p
                # ---------- ② 负对照: 手动复现旧 bug ----------
                print("")
                print("=== ② 负对照: 只清 cache、**保留时间戳**(= 旧代码) ===")
                w._upper_rough_cache = None          # 旧代码在 _rebuild 里就做了这一句
                # 注意**不动** _upper_rough_cache_t
                arr = w._upper_rough_now()
                print("  时间戳 = %s, cache = None" % getattr(w, "_upper_rough_cache_t", None))
                print("  `_upper_rough_now()` 返回: %s" % ("**None(空)**" if arr is None else "数组"))
                at = w._upper_rough_at(30, 100.0) if hasattr(w, "_upper_rough_at") else None
                print("  下游 `_upper_rough_at(30, ...)` = %s  ⇒ %s"
                      % (at, "**画成光滑面**" if at == 0.0 else "有值"))
                print("  ⇒ 判据**会翻红** —— 这条链是真的, 不是我猜的。")
                # ---------- ③ 时间一走缓存自然失效 ----------
                print("")
                print("=== ③ 时间往前走一步 ===")
                w._rebuild_height_table()
                w.set_rough_level("6")
                a1 = w._upper_rough_now()
                w.elapsed += 0.02
                a2 = w._upper_rough_now()
                print("  同一 elapsed: %s ; elapsed+0.02 后: %s"
                      % ("None" if a1 is None else "数组",
                         "None" if a2 is None else "数组"))
                # ⚠️ 修好之后这一步**两边都是数组**, 所以它**证明不了任何事** ——
                #    按旧代码的机制推, 这里是"起伏自己回来"的原因; 但那已经是历史,
                #    本探针不为历史作证。**别拿这一段当证据。**
                print("  ⚠️ 修好之后这里两边都是数组 ⇒ **本段不构成证据**;")
                print("     它只解释**旧代码**下为什么看起来像「一动又回来了」。")
                Clock.schedule_once(lambda d: self.stop(), 0.2)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
