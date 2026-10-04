# -*- coding: utf-8 -*-
"""下球沙堆形状的**几何验收**(专家 xingzhuang2.md §13.2 + dingbu.md §10)。

2026-10-04 起轮廓改版: 取消平台, 改"微不对称尖堆 + 受限微粗糙"(dingbu.md §3)。
所以旧的"左右对称"判据**作废** —— 不对称是设计, 改为"不对称度不超过设计上限"。

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
        shape = m._mound_shape_array(Ri)
        prof = m._MoundProfile(Ri, shape)
        print("Ri=%.3f  轮廓 %d 点  左坡 %.3f / 右坡 %.3f  粗糙上限 %.2f px"
              % (Ri, len(shape), m.MOUND_SLOPE_L, m.MOUND_SLOPE_R,
                 m.MOUND_ROUGH_FRAC * 2 * Ri))

        fails = []

        def check(name, ok, detail=""):
            print("CHECK %-48s %s %s" % (name, "PASS" if ok else "**FAIL**", detail))
            if not ok:
                fails.append(name)

        # ① 空: h=0 → apex=0
        check("h=0 → apex=0(无沙)", abs(prof.apex_for_height(0.0)) < 1e-9,
              "%.6f" % prof.apex_for_height(0.0))

        # ② 没有平台: 中心就是最高点, 且从中心向两侧**严格下降**
        n = 1001
        prev = -1e9
        worst_back = 0.0
        worst_out = 0.0
        for i in range(n):
            h = 2.0 * Ri * i / (n - 1)
            a = prof.apex_for_height(h)
            if a < prev - 1e-9:
                worst_back = max(worst_back, prev - a)
            prev = a
            for dx in (0.0, 20.0, 60.0, Ri * 0.5, Ri * 0.9, Ri):
                lo, hi = prof.bounds(dx)
                c = prof.contact(dx, a)
                worst_out = max(worst_out, lo - c, c - hi)
        check("apex 随 h 单调不倒退", worst_back < 1e-9, "最大倒退 %.3e px" % worst_back)
        check("接触高度恒在 [球内底, 球内顶]", worst_out < 1e-9, "最大越界 %.3e px" % worst_out)

        # ⚠️ 判据方向: 从**中心轴**向两侧走, raw 必须严格下降(不是从 -R 向 +R 扫 ——
        #    那样左半边本来就该是升的, 会假报逆坡)。
        a_mid = prof.apex_for_height(0.5 * Ri)
        step = Ri / 137.0
        worst_rise = 0.0
        for sign in (1.0, -1.0):
            for k in range(0, 136):
                d = prof.raw(sign * (k + 1) * step, a_mid) - prof.raw(sign * k * step, a_mid)
                if d > 1e-9:
                    worst_rise = max(worst_rise, d)
        check("无平台: 从中心向两侧严格下降", worst_rise < 1e-9,
              "最大逆坡 %+.4f px/格" % worst_rise)

        # ③ 不对称是**设计**(0.58/0.62), 但必须有上限: 两侧差 ≤ 斜率差×|x| + 2×粗糙
        worst_asym = 0.0
        limit_asym = 0.0
        # ⚠️ 必须量**未裁剪**的 raw: contact 在近壁处被球底/球顶夹住 ⇒ 两侧夹成同一个值,
        #    量出来恒 0(踩过 —— 这个探针一开始是废的)。
        for dx in (20.0, 60.0, Ri * 0.5, Ri * 0.9):
            d = abs(prof.raw(dx, a_mid) - prof.raw(-dx, a_mid))
            allow = abs(m.MOUND_SLOPE_R - m.MOUND_SLOPE_L) * dx \
                + 2 * m.MOUND_ROUGH_FRAC * 2 * Ri
            worst_asym = max(worst_asym, d)
            limit_asym = max(limit_asym, allow)
        check("左右不对称在设计上限内", worst_asym <= limit_asym + 1e-6,
              "实测最大 %.2f px ≤ 上限 %.2f px" % (worst_asym, limit_asym))

        # ④ 满: h=2R → 没有自由表面(完整圆)
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
        check("h=2R → 每列都填满", thick_bad < 0.05, "最大差 %.3f px" % thick_bad)

        # ⑤ 面积口径自洽
        worst_frac = 0.0
        for i in range(0, 101):
            h = 2.0 * Ri * i / 100.0
            a = prof.apex_for_height(h)
            got = prof.heap.area_at(a / Ri) / prof.heap.capacity
            want = prof.flat.area_at(h / Ri) / prof.flat.capacity
            worst_frac = max(worst_frac, abs(got - want))
        check("面积口径自洽(反查后填满比=目标)", worst_frac < 2e-3,
              "最大差 %.5f (=%.2f 个百分点)" % (worst_frac, worst_frac * 100))

        # ⑥ 虚拟峰高允许超过球顶
        print("INFO  h=2R 时虚拟峰高 apex=%.2f px (球内高 2R=%.2f)" % (apex_full, 2.0 * Ri))
        check("虚拟峰高允许高于球顶", apex_full > 2.0 * Ri - 1e-6,
              "apex-2R = %+.2f px" % (apex_full - 2.0 * Ri))

        # ⑦ 实测坡度 ≈ 设计值(取远离中心的段, 避开粗糙)
        far = 0.6 * Ri
        sl = (prof.raw(-far, a_mid) - prof.raw(-far - 20.0, a_mid)) / 20.0
        sr = (prof.raw(far, a_mid) - prof.raw(far + 20.0, a_mid)) / 20.0
        check("左坡 ≈ %.2f" % m.MOUND_SLOPE_L, abs(sl - m.MOUND_SLOPE_L) < 0.05, "实测 %.3f" % sl)
        check("右坡 ≈ %.2f" % m.MOUND_SLOPE_R, abs(sr - m.MOUND_SLOPE_R) < 0.05, "实测 %.3f" % sr)

        print("几何验收 ==== %s ====" % ("全部通过" if not fails else "失败: %s" % fails))
        return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
