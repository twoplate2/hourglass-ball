"""量粒子物理这一步的**净**收益: 标量循环 vs (向量化 + 命中回放 + 压缩 + dict 同步)。

为什么要算"净": 向量化本身只解决了 5.79ms 里的算术部分, 但 NUMPY_PLAN 步骤 3/4 还没做,
`_p_sync_dicts()` 每帧仍要新建 ~2500 个 dict 把数组同步回老接口。只报向量化耗时是骗人的。

本机 CPython 与设备上是同一个解释器实现, 所以**比值**有参考价值(绝对毫秒不可比:
MuMu 的 CPU 比本机慢)。同时给出通量(颗粒/秒), 便于换算到设备的 2500 颗粒工况。

用法: python tools/bench_particles.py [颗粒数]
"""

import math
import os
import random
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

import flow_numpy
from test_physics_equiv import ref_step


def make_population(n, c, rng):
    py = [rng.uniform(c["lower_bot"] - 20, c["gen_y"]) for _ in range(n)]
    pvy = [rng.uniform(-500.0, 60.0) for _ in range(n)]
    pxo = [rng.uniform(-6.0, 6.0) for _ in range(n)]
    pwp = [rng.uniform(0, math.tau) for _ in range(n)]
    pwa = [rng.uniform(0.4, 1.0) for _ in range(n)]
    psz = [2.0 if rng.random() < 0.85 else 1.0 for _ in range(n)]
    return py, pvy, pxo, pwp, pwa, psz


def sync_dicts(px, py, pvy, pxo, pwp, pwa, psz, ptl, pli, pn):
    """与 main.py:_p_sync_dicts 同形的兼容层(步骤 3/4 完成后会删掉)。"""
    particles = []
    append = particles.append
    for i in range(pn):
        append({
            "x": float(px[i]), "y": float(py[i]), "vy": float(pvy[i]),
            "x_offset": float(pxo[i]), "wobble_phase": float(pwp[i]),
            "wobble_amp": float(pwa[i]), "size": int(psz[i]),
            "trail_time": float(ptl[i]), "is_light": bool(pli[i]),
        })
    return particles


def timeit(fn, repeats):
    best = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        if best is None or dt < best:
            best = dt
    return best


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2500
    rng = random.Random(23)
    np.random.seed(23)

    c = {
        "cx": 200.0, "gen_y": 420.0, "mound_top": 330.0,
        "g": -450.0, "g_abs": 450.0,
        "source_speed": 60.0, "source_speed_squared": 3600.0,
        "lower_cut": 300.0, "lower_top": 260.0, "lower_center": 150.0,
        "Ri2": 160.0 ** 2, "tube_lim": 6.0, "lower_bot": 40.0,
        "peak_offset": 0.0,
    }
    dt = 1.0 / 60.0
    py, pvy, pxo, pwp, pwa, psz = make_population(n, c, rng)
    px = [c["cx"]] * n
    ptl = [0.025] * n
    pli = [0.0] * n
    pdt = [dt] * n

    npx = np.array(px); npy = np.array(py); npvy = np.array(pvy)
    npxo = np.array(pxo); npwp = np.array(pwp); npwa = np.array(pwa)
    npsz = np.array(psz); nptl = np.array(ptl); npli = np.array(pli)
    npdt = np.array(pdt)

    REPEAT = 30

    def scalar():
        ref_step(px[:], py[:], pvy[:], pxo[:], pwp[:], pwa[:], psz[:], pdt[:],
                 dt, dict(c))

    def vector_only():
        flow_numpy.step(npx, npy, npvy, npxo, npwp, npwa, npsz, npdt, n, dict(c))

    def compat():
        sync_dicts(npx, npy, npvy, npxo, npwp, npwa, npsz, nptl, npli, n)

    t_scalar = timeit(scalar, REPEAT)
    t_vec = timeit(vector_only, REPEAT)
    t_sync = timeit(compat, REPEAT)
    t_net = timeit(lambda: (vector_only(), compat()), REPEAT)

    print("颗粒数 n = %d,   每档取 %d 次里最快的一次" % (n, REPEAT))
    print("  标量循环(现状)          %8.3f ms/帧" % (t_scalar * 1e3))
    print("  仅向量化内核            %8.3f ms/帧" % (t_vec * 1e3))
    print("  仅 dict 兼容层(过渡期)  %8.3f ms/帧" % (t_sync * 1e3))
    print("  ---------------- 净 ---------------------------------")
    print("  向量化 + 兼容层(现在)   %8.3f ms/帧   加速 %.2fx" % (t_net * 1e3, t_scalar / t_net))
    print("  向量化 无兼容层(步骤3/4) %8.3f ms/帧   加速 %.2fx" % (t_vec * 1e3, t_scalar / t_vec))
    print()
    print("  通量: 标量 %.0f 颗粒/ms   向量化 %.0f 颗粒/ms" % (n / (t_scalar * 1e3), n / (t_vec * 1e3)))


if __name__ == "__main__":
    main()
