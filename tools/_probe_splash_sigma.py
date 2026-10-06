# -*- coding: utf-8 -*-
"""飞溅**横向分布**现在是多少个标准差 —— 用 σ 当刻度说话。

用户 2026-10-06: 「按正态分布来, 最末端/最边缘差不多是 1.5 个标准差」;
                 「后续我们说怎么调整, 直接说标准差即可」。

所以本探针只回答两件事:
  ① **沙堆边缘在哪** (= `has_sand` 为真的最远 |dx|) —— 这是"最边缘"的位置。
  ② **在途飞溅的横向分布**: |dx| 的 p50/p90/p99/max/std,
     以及 **边缘 / σ_实测 = 几个标准差** —— 这就是用户要的那个数。

当前抽样(读代码, main.py:2914):
    u ~ U(0,1);  mag = 0.96·Ri·u^SPLASH_BG_POW (POW=0.9), 随机取正负
    ⇒ |j| = |mag|/Ri 的 CDF 是 (x/0.96)^(1/0.9) ⇒ **几乎均匀**, 不是高斯。
    再被 `has_sand` 硬截断到沙堆宽度。
⚠️ 所以现在的"σ"没有分布含义, 只是个离散度; 报告它是为了**给改前的基线一个数**。

跑法: python tools/_probe_splash_sigma.py [周期, 默认 15]
"""
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_one(duration):
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    sys.argv = [sys.argv[0], "_sigma_probe"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 50}
    m.HourglassWidget.save_config = lambda *_: None

    # 在**生成时**给背景层的飞溅打标记(不改 main.py): 包一层 `_spawn_bg_splashes`,
    # 记下调用前后的长度差, 把新增的那些标上 `_bg`。
    _orig_bg = m.HourglassWidget._spawn_bg_splashes

    def _bg_marked(self, dt):
        n0 = len(self.splashes)
        _orig_bg(self, dt)
        for sp in self.splashes[n0:]:
            sp["_bg"] = 1
        # 生成瞬间的位置 —— 绕开一切存活/老化效应
        if self.running and duration * 0.35 <= self.elapsed <= duration * 0.65:
            for sp in self.splashes[n0:]:
                spawn.append(abs(sp["x"] - self._cx))

    m.HourglassWidget._spawn_bg_splashes = _bg_marked

    spawn = []
    acc = {"hit": [], "bg": [], "edge": [], "apex": []}

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            hg = self.hourglass
            hg.duration = duration
            hg._rebuild_height_table()
            hg.toggle()

            def sample(*_a):
                if not hg.running:
                    return
                t = float(hg.elapsed)
                if not (duration * 0.35 <= t <= duration * 0.65):
                    return
                cx = hg._cx
                for s in hg.splashes:
                    # ⚠️ 别用 `_step_dt` 区分 —— 它是**用完就 pop 的**(见 update_particles),
                    #    第一帧之后命中路径的飞溅也不带它 ⇒ 会把命中层全判成背景层
                    #    (实测"命中占 0%"就是这条错判据的产物)。
                    #    改用包装器在**生成时**打的 `_bg` 标记(见 run_one)。
                    k = "bg" if s.get("_bg") else "hit"
                    acc[k].append(abs(s["x"] - cx))
                # 沙堆边缘: 从中心往外走, 找最后一个 has_sand 为真的 |dx|
                prof = hg._mound_profile
                apex = hg._mound_apex()
                if prof is not None and apex > 0.0:
                    e = 0.0
                    x = 0.0
                    while x < hg._R_inner:
                        if prof.has_sand(x, apex):
                            e = x
                        x += 1.0
                    acc["edge"].append(e)
                    acc["apex"].append(apex)

            Clock.schedule_interval(sample, 1.0 / 30.0)
            Clock.schedule_once(lambda dt: self.stop(), duration + 0.5)

    P().run()
    return acc, spawn


def main():
    d = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    acc, spawn = run_one(d)
    if not acc["hit"] and not acc["bg"]:
        print("  没采到飞溅"); return 1
    edge = statistics.median(acc["edge"]) if acc["edge"] else float("nan")
    apex = statistics.median(acc["apex"]) if acc["apex"] else float("nan")

    # 截断半正态(在 ±kσ 处截断)的理论常数 —— 用它们从分位数**反推 σ_scale**。
    # ⚠️ 不能用 `pstdev(|dx|)` 当 σ! 半正态的 std = 0.406σ(截断到 1.5σ时),
    #    直接拿它去比"边缘是几个 σ"会得出 3.7 这种假数(本探针 v1 就这么错过一次)。
    try:
        from scipy.stats import norm as _n  # noqa
        _have = True
    except Exception:
        _have = False
    K = 1.5
    # P(|z|<=K)= 2Φ(K)-1 ; 条件分位数 p ⇒ z_p = Φ⁻¹((1 + p·(2Φ(K)-1))/2)
    import math as _math

    def _phi(x):
        return 0.5 * (1.0 + _math.erf(x / _math.sqrt(2.0)))

    def _phi_inv(p):
        # 二分求逆(够用且不引依赖)
        lo, hi = -10.0, 10.0
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if _phi(mid) < p:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    _pz = 2.0 * _phi(K) - 1.0
    _q50z = _phi_inv(0.5 * (1.0 + 0.50 * _pz))     # 截断后 |z| 的中位
    _q90z = _phi_inv(0.5 * (1.0 + 0.90 * _pz))

    def stat(name, dx, note="", sigma_meaningful=False):
        if not dx:
            print("  %-6s (无)" % name); return
        d = sorted(dx); n = len(d)
        q = lambda p: d[min(n - 1, int(p * n))]
        sd = statistics.pstdev(d)
        sig = q(.50) / _q50z if _q50z > 0 else float("nan")
        # 🔴 「反推 σ / 边缘÷σ」这两列**只对"生成端(单峰截断高斯)"有分布含义**
        #    (1号专家 2026-10-06 指出): 命中层是**被沙柱宽度限死的有界分布**、全场是
        #    "单峰+重肩"的混合 —— 把截断半正态的分位常数套上去算出来的"σ"没有意义,
        #    **不能拿来跟 1.5 比**。旧版把它印在每一行、末尾还写"边缘/σ=6.42",
        #    与上一行「按定义成立」**自相矛盾**, 只看最后一行会得出相反结论。
        cols = ("反推σ=%5.1f | 边缘/σ=%4.2f" % (sig, edge / sig) if sigma_meaningful
                else "反推σ=  n/a | 边缘/σ= n/a (非单峰/有界分布, σ 无分布含义)")
        print("  %-6s n=%-6d p50=%6.1f p90=%6.1f p99=%6.1f max=%6.1f | %s  %s"
              % (name, n, q(.50), q(.90), q(.99), d[-1], cols, note))

    print("")
    print("  === 周期 %.0fs  在途飞溅横向分布 ===" % d)
    print("  沙堆峰值 apex = %.1f px   沙堆边缘 |dx| = %.1f px  <- 「最边缘」" % (apex, edge))
    print("  代码设定: σ = 边缘/1.5 = %.1f px  ⇒ 「边缘 = 1.5σ」按定义成立" % (edge / 1.5))
    print("  (截断半正态: p50 = %.4fσ, p90 = %.4fσ —— 用它从分位数反推实测 σ)" % (_q50z, _q90z))
    print("")
    print("  === 周期 %.0fs  在途飞溅横向分布 ===" % d)
    print("  沙堆峰值 apex = %.1f px   沙堆边缘 |dx| = %.1f px  <- 「最边缘」" % (apex, edge))
    print("")
    stat("全部", acc["hit"] + acc["bg"], "<- 眼睛看到的场(混合, 非高斯)")
    stat("命中", acc["hit"])
    stat("背景", acc["bg"])
    tot = len(acc["hit"]) + len(acc["bg"])
    print("")
    print("  --- 背景层**生成瞬间**(无存活偏置) ---")
    stat("生成", spawn, "<- 无存活偏置; **只有这一行** σ 有分布含义",
         sigma_meaningful=True)
    print("")
    print("  命中占 %.0f%% / 背景占 %.0f%%" % (100.0*len(acc["hit"])/tot, 100.0*len(acc["bg"])/tot))
    print("")
    print("  ⚠️ 「边缘/σ」**只对『生成』那一行有意义** —— 它按定义就是 边缘/1.5。")
    print("     其余行的 σ 是把截断半正态的分位常数硬套在别的形状上算的, **不可比 1.5**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
