# -*- coding: utf-8 -*-
"""下球沙堆形状改动的**每帧代价**(专家 xinghuang2.md §13.3: 形状计算/图元更新/整帧分开记)。

做法: 不起窗口、只 import main, 在纯 Python 侧重复调用形状相关的那几个函数, 用 timeit 计时。
⚠️ 这**不能**替代真机/模拟器的整帧测量(那要上 benchmark); 它只回答"形状解本身贵不贵"。

跑法: python tools/_xz_shape_cost.py
"""
import os
import sys
import tempfile
import timeit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="xz-cost-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m

        Ri = 136.701
        shape = m._mound_shape_array(Ri)
        prof = m._MoundProfile(Ri, shape)

        def build():
            m._MoundProfile(Ri, shape)

        def solve():
            prof.apex_for_height(180.0)

        def contacts():
            apex = prof.apex_for_height(180.0)
            for i in range(52):                       # 52 个绘制节点
                dx = -Ri + 2.0 * Ri * i / 51.0
                prof.contact(dx, apex)
                prof.free_surface(dx, apex)

        def crossings():
            apex = prof.apex_for_height(180.0)
            lo, hi = 1e-6, Ri   # 二分下界 = 轮廓峰值(中心轴)
            for _ in range(18):                        # 二分 18 次 × 两侧
                mid = 0.5 * (lo + hi)
                if prof.raw(mid, apex) - prof.bounds(mid)[0] > 0.0:
                    lo = mid
                else:
                    hi = mid

        def bench(fn, n=2000):
            return timeit.timeit(fn, number=n) / n * 1e6      # µs

        t_build = bench(build, 200)
        t_solve = bench(solve)
        t_contact = bench(contacts)
        t_cross = bench(crossings)
        per_frame = t_solve + t_contact + 2 * t_cross          # 每帧: 解一次 + 52 节点 + 左右各一次求根
        print("_MoundProfile 构造(只在几何变化时)   %8.1f µs" % t_build)
        print("apex_for_height(查表求逆)            %8.2f µs" % t_solve)
        print("52 个节点的 contact+free_surface     %8.2f µs" % t_contact)
        print("壁交点二分(单侧 18 次)                %8.2f µs" % t_cross)
        print("-" * 46)
        print("每帧形状合计 ≈ %.1f µs = %.3f ms   (目标 ≤0.2ms)"
              % (per_frame, per_frame / 1000.0))
        return 0


if __name__ == "__main__":
    sys.exit(main())
