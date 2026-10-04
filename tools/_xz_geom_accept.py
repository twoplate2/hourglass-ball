# -*- coding: utf-8 -*-
"""下球沙堆形状的**几何验收**(专家 xingzhuang2.md §13.2 的几何那几条)。

只验几何, 不跑 Kivy 应用; 但 main.py 顶部 import Kivy, 所以照旧设 KIVY_HOME 到临时目录。

跑法: python tools/_xz_geom_accept.py
"""
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="xz-geom-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m

        Ri = 136.701          # 380x631 窗口实测(与信的 §1 一致)
        b = min(Ri * 0.5, max(m.MOUND_PLATEAU_MIN, m.MOUND_PLATEAU_K * 10.94))
        prof = m._MoundProfile(Ri, b, m.MOUND_REPOSE_SLOPE)
        print("Ri=%.3f  平台半宽 b=%.2f  斜率=%.2f  节点 %d 个"
              % (Ri, b, m.MOUND_REPOSE_SLOPE, len(prof.xs)))

        fails = []

        def check(name, ok, detail=""):
            print("CHECK %-46s %s %s" % (name, "PASS" if ok else "**FAIL**", detail))
            if not ok:
                fails.append(name)

        # ① 空: h=0 → 锥顶在地面, 没有任何沙
        apex0 = prof.apex_for_height(0.0)
        check("h=0 → apex=0(无沙)", abs(apex0) < 1e-9, "%.6f" % apex0)

        # ② 单调 + 不倒退 + 接触高度不越球底/球顶 + 左右对称
        n = 1001
        prev = -1e9
        worst_back = 0.0
        worst_out = 0.0
        worst_asym = 0.0
        for i in range(n):
            h = 2.0 * Ri * i / (n - 1)
            a = prof.apex_for_height(h)
            if a < prev - 1e-9:
                worst_back = max(worst_back, prev - a)
            prev = a
            for dx in (0.0, b * 0.5, b, b * 1.5, Ri * 0.5, Ri * 0.9, Ri):
                lo, hi = prof.bounds(dx)
                c = prof.contact(dx, a)
                worst_out = max(worst_out, lo - c, c - hi)
                c2 = prof.contact(-dx, a)
                worst_asym = max(worst_asym, abs(c - c2))
        check("apex 随 h 单调不倒退", worst_back < 1e-9, "最大倒退 %.3e px" % worst_back)
        check("接触高度恒在 [球内底, 球内顶]", worst_out < 1e-9, "最大越界 %.3e px" % worst_out)
        check("接触高度左右对称", worst_asym < 1e-9, "最大不对称 %.3e px" % worst_asym)

        # ③ 满: h=2R → 每一列都填满(自由表面为零), 且是完整圆
        apex_full = prof.apex_for_height(2.0 * Ri)
        free_cols = 0
        thick_bad = 0.0
        for i in range(401):
            dx = -Ri + 2.0 * Ri * i / 400.0
            lo, hi = prof.bounds(dx)
            c = prof.contact(dx, apex_full)
            if prof.free_surface(dx, apex_full):
                free_cols += 1
            thick_bad = max(thick_bad, abs((c - lo) - (hi - lo)))
        check("h=2R → 没有自由表面(满球)", free_cols == 0, "自由表面列数 %d" % free_cols)
        check("h=2R → 每列都填满(厚度=球内高)", thick_bad < 0.05, "最大差 %.3f px" % thick_bad)

        # ④ 口径自洽: 用同一张表反查的填充比, 与目标比对(不是解析圆弓)
        worst_frac = 0.0
        for i in range(0, 101):
            h = 2.0 * Ri * i / 100.0
            a = prof.apex_for_height(h)
            got = prof.heap.area_at(a / Ri) / prof.heap.capacity
            want = prof.flat.area_at(h / Ri) / prof.flat.capacity
            worst_frac = max(worst_frac, abs(got - want))
        check("面积口径自洽(反查后填满比=目标)", worst_frac < 2e-3,
              "最大差 %.5f (=%.2f 个百分点)" % (worst_frac, worst_frac * 100))

        # ⑤ 虚顶允许超过球顶(第一版的求解上界错的直接证据)
        print("INFO  h=2R 时的虚拟锥顶 apex=%.2f px (球内高 2R=%.2f)"
              % (apex_full, 2.0 * Ri))
        check("虚拟锥顶允许高于球顶", apex_full > 2.0 * Ri - 1e-6,
              "apex-2R = %+.2f px" % (apex_full - 2.0 * Ri))

        # ⑥ 中段是不是"堆": 平台之外单调下降, 且斜率 ≈ 0.6
        a_mid = prof.apex_for_height(0.5 * Ri)
        ys = [(dx, prof.contact(dx, a_mid)) for dx in (b + 10.0, b + 60.0)]
        slope = (ys[0][1] - ys[1][1]) / (ys[1][0] - ys[0][0])
        check("斜坡斜率 ≈ 0.60", abs(slope - 0.60) < 0.02, "实测 %.4f" % slope)

        print("几何验收 ==== %s ====" % ("全部通过" if not fails else "失败: %s" % fails))
        return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
