# -*- coding: utf-8 -*-
"""**随机取整**的两条硬判据(2026-10-09)。

改的是 `_first_contact_extra()`(替换原来的 `round(2 * _splash_density)`)。

## 判据 ①(结构性): 最粗档**一个随机数都不许抽**
`density == 1.5` 时 `want = 3.0`、`frac = 0.0` ⇒ 必须靠 `frac > 0.0` 的短路跳过 `rand()`。
用 `random.getstate()` 前后比对来判 —— **状态没变 = 没抽**。
⚠️ 若写成 `rand() < frac`(不短路的写法), 这一条会**翻红**: 状态变了、整条流平移,
用户确认过的"粗柱(≤5s)保持 2.9 + 加量 150%"那一档会跟着变。
负对照: 把 `and` 左边那句删掉(见文件尾的 `--neg` 用法说明)即应翻红。

## 判据 ②(统计性): 长期平均 == 2 × density
原来 `round(2d)` 在整条时长轴上只有 {0,1,2,3} 四个值; 改后均值应**精确**贴合 `2d`,
且**逐点无台阶**。这里取多档采样比对。

跑法: python tools/_probe_first_contact_extra.py
"""
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

from kivy.clock import Clock                     # noqa: E402
from kivy.core.window import Window              # noqa: E402
import main as m                                 # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *a, **kw: None
m.HourglassWidget._make_completion_sound = lambda *a, **kw: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 60000}
m.HourglassWidget.save_config = lambda *_: None

N = 20000
TIERS = [1.0, 5.0, 10.0, 30.0, 60.0, 600.0, 3600.0, 36000.0]
ok = []


class Probe(m.HourglassApp):
    def on_start(self):
        Window.size = (1904, 2890)
        Clock.schedule_once(self.go, 1.2)

    def go(self, _dt):
        hg = self.hourglass
        print("")
        print("  判据 ①: density==1.5 时必须**零随机数消耗**(状态不变)")
        print("  判据 ②: 长期平均必须 == 2 × density(原 round() 只有 4 个取值)")
        print("")
        print("  %9s %9s %8s | %10s %12s | %9s %10s" %
              ("时长", "density", "2×dens", "均值", "偏差", "RNG消耗", "旧行为"))
        for d in TIERS:
            hg.set_duration(d)
            dens = hg._splash_density
            want = 2.0 * dens
            random.seed(12345)
            before = random.getstate()
            draws = [hg._first_contact_extra(random.random) for _ in range(N)]
            after = random.getstate()
            consumed = (before != after)
            mean = sum(draws) / float(N)
            dev = mean - want
            old = round(want)
            good = (abs(dev) < 0.05) and (not consumed or dens < 1.5)
            ok.append(good)
            print("  %9s %9.4f %8.3f | %10.4f %12.4f | %9s %10d %s"
                  % ("%gs" % d, dens, want, mean, dev,
                     "有" if consumed else "无", old, "" if good else "  <== 不合格"))
            if dens >= 1.5:
                assert not consumed, "最粗档抽了随机数 ⇒ 随机流会被平移!"
                assert set(draws) == {3}, "最粗档不是恒定 3 颗!"
        # 判据 ③: 台阶消失 —— 扫 density 全区间, 看均值曲线是否单调平滑(旧 round 是阶梯)
        print("")
        print("  判据 ③: 扫 density 0.15→1.50, 均值曲线 vs 旧 round() 曲线")
        steps_old = set()
        d = 0.15
        while d <= 1.5001:
            hg._splash_density = d
            random.seed(7)
            mu = sum(hg._first_contact_extra(random.random) for _ in range(2000)) / 2000.0
            steps_old.add(round(2 * d))
            if abs(mu - 2 * d) > 0.06:
                print("     !! density=%.3f 均值 %.3f 偏离期望 %.3f" % (d, mu, 2 * d))
                ok.append(False)
            d += 0.05
        print("     旧 round(2d) 在整条区间上只取这些值: %s (共 %d 个)"
              % (sorted(steps_old), len(steps_old)))
        print("     新均值曲线: 全程 |均值 − 2d| < 0.06  ⇒ 无台阶" if all(ok)
              else "     **有超差**")
        print("")
        print("  ==> %s (%d/%d)" % ("全部通过" if all(ok) else "**有失败**",
                                    sum(ok), len(ok)))
        self.stop()


Probe().run()
