# -*- coding: utf-8 -*-
"""数字与文案层的**系统性**扫描 —— 纯桌面, 不碰设备。

被扫的四个出口（都在 main.py 里, 同一批周期数值喂进去）:
  ① `_fmt_countdown_pair(remaining, duration)`  主界面倒计时
  ② `_fmt_duration(duration)`                   底部周期按钮
  ③ `_fmt_duration_cn(duration)`                完成弹窗大字「用时：…」
  ④ `_duration_tokens(sec)` → 完成播报的**词序**（中文读法拼接, 最容易出错的一块）

要查的:
  · **往返一致**: ② 印出来的字能不能被 `_closest_base_and_mult` 解回原值?
    （解不回去 ⇒ 用户看到"1.0 分钟"，但实际还是 61 秒，或者反过来）
  · **边界**: 59/60/61、3599/3600/3601、86399/86400、359999/360000
  · **④ 的中文读法**: 十位补"一"的规则（10→十 但 110→一百一十）、零分量省略、
    `hour` 与 `sec` 之间夹「零」的规则 —— 逐条打出来看有没有读错的
  · **空/越界**: 0、负数、MAX_DURATION 之下

跑法: python tools/_probe_text_formats.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = [0, 1, 2, 9, 10, 11, 59, 60, 61, 62, 119, 120, 121,
         599, 600, 601, 3599, 3600, 3601, 3661, 7200,
         35999, 36000, 86399, 86400, 90061, 359999, 360000]


def main():
    with tempfile.TemporaryDirectory(prefix="textfmt-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        # ⚠️ 播报的词序在 `_VoiceBank.sentence_keys()` 里, **不是** `_duration_tokens`
        #    （那是 pc v4 的函数; 第一版探针照它写, 直接 AttributeError）。
        bank = m._VoiceBank(str(ROOT))
        print("  词库加载: %s (rate=%s, %d 个词块)"
              % ("ok" if bank.ok else "**失败(会回退整句)**", bank.rate, len(bank.clips)))

        print("\n=== ① ② ③ 三个出口 (秒 → 各自印出来什么) ===")
        head = "%-9s %-14s %-12s %-22s %s"
        print(head % ("秒", "进度条?", "周期按钮", "完成弹窗大字", "播报词序"))
        print("-" * 96)
        bad = []
        for s in PROBE:
            btn = m._fmt_duration(s)
            cn = m._fmt_duration_cn(s)
            try:
                toks = bank.sentence_keys(s)
                miss = [k for k in toks if k not in bank.clips]
                if miss:
                    bad.append((s, "词块缺失: %s" % miss))
            except Exception as exc:
                toks = ["<%s: %s>" % (type(exc).__name__, exc)]
                bad.append((s, "词序抛异常"))
            print("%-9d %-14s %-12s %-22s %s" % (s, "-", btn, "用时：" + cn, toks))

        print("")
        print("=== ②′ 往返: **UI 真能设出来的值**（基础 × 倍数）能不能再解回同一个总数 ===")
        print("  ⚠️ 第一版拿 3599 这种数去测 —— 而周期只能是 `基础×倍数`, UI 根本设不出 3599,")
        print("     所以那个测法测的是不存在的场景。判据改成有意义的那条: **乘积能不能往返**。")
        cb = m.HourglassApp._closest_base_and_mult      # 不依赖 self, 可当普通函数调
        n_ok = n_bad = 0
        for _label, base_val in m.BASE_PERIODS:
            for mult in (1, 2, 3, 7, 59, 60, 100, 359, 600):
                total = base_val * mult
                if total > m.MAX_DURATION or mult > m.MULT_SLIDER_MAX:
                    continue
                b2, m2 = cb(None, total)
                if b2 * m2 == total:
                    n_ok += 1
                else:
                    n_bad += 1
                    bad.append((total, "往返: %d → %s×%s = %d" % (total, b2, m2, b2 * m2)))
        print("  %d 个能往返 / %d 个不能" % (n_ok, n_bad))

        print("")
        print("=== ④ 中文读法的重点边界（人眼逐条看）===")
        for s in (10, 11, 20, 60, 110, 111, 101, 100, 120, 600, 601, 3600, 3661, 359999):
            t = bank.sentence_keys(s)
            print("  %-7d %s" % (s, " + ".join(t)))

        print("")
        print("=== ⚠️ 机器人该报的 ===")
        if bad:
            for s, why in bad:
                print("  **%d**: %s" % (s, why))
        else:
            print("  无（往返全部一致, 无抛异常）")
        print("")
        print("注: 上面只判**往返与异常**这类机器能判的; 「读起来自不自然」要人看,")
        print("    所以第四节把词序原样打了出来, 没有自动判它对不对。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
