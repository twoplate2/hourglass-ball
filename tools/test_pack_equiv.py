"""验证「向量化打包」与「逐颗粒 struct.pack_into」的字节输出逐位相同。

这是 `tools/flow_texture_experiment.py:TextureFlowBatch.update` 那条快路径的验收:
同一条公式, 两种写法, 必须产出**完全相同的字节**。任何一处不等都意味着画面会变。

覆盖:
- astype('<f4') vs struct.pack('<f') 的逐位一致(含 0/-0/inf/denormal/float32 极值)
- 整段公式: vy 取绝对值 → trail = vy*tl/ms → 下限 2 → top = bottom+trail → 上限 top_limit
- 边界: vy = -0.0 / 0.0、trail 恰为 2、top 恰为 top_limit、tl = 0

用法: python tools/test_pack_equiv.py
"""

import random
import struct
import sys

import numpy as np

FLOAT3 = struct.Struct("<3f")


def scalar_pack(xs, ys, vys, tls, ms, tl_lim):
    n = len(xs)
    out = bytearray(n * 12)
    pack = FLOAT3.pack_into
    off = 0
    for k in range(n):
        bottom = ys[k]
        vy = vys[k]
        if vy < 0:
            vy = -vy
        trail = vy * tls[k] / ms
        if trail < 2:
            trail = 2
        top = bottom + trail
        if top > tl_lim:
            top = tl_lim
        pack(out, off, xs[k], bottom, top)
        off += 12
    return bytes(out)


def vector_pack(xs, ys, vys, tls, ms, tl_lim):
    a_x = np.array(xs)
    bottom = np.array(ys)
    vy = np.abs(np.array(vys))
    trail = vy * np.array(tls) / ms
    np.maximum(trail, 2.0, out=trail)
    top = bottom + trail
    np.minimum(top, tl_lim, out=top)
    blk = np.empty((len(xs), 3), dtype=np.float64)
    blk[:, 0] = a_x
    blk[:, 1] = bottom
    blk[:, 2] = top
    return blk.astype("<f4").tobytes()


def check_float32_cast():
    rng = random.Random(29)
    vals = [rng.uniform(-1e5, 1e5) for _ in range(200000)]
    vals += [0.0, -0.0, 1.0, -1.0, 1e-45, -1e-45, 5e-324, 1.17549435e-38,
             3.4028235e38, float("inf"), float("-inf"), 1 / 3, 2 ** -149, 0.1]
    conv = np.array(vals, dtype=np.float64).astype("<f4").view(np.uint8).reshape(-1, 4)
    bad = 0
    for i, v in enumerate(vals):
        if struct.pack("<f", v) != conv[i].tobytes():
            bad += 1
    print("astype('<f4') vs struct.pack('<f'): %d 样本, 不一致 %d" % (len(vals), bad))
    return bad == 0


def check_formula():
    rng = random.Random(31)
    cases = [
        (4000, 1.37, 1712.5),      # 15 秒档的 motion_scale / 直筒下端
        (2900, 1.0, 1712.5),
        (300, 2.5, 400.0),        # 1 秒档(短周期, motion_scale 大)
        (1, 1.0, 0.0),            # 退化: 单颗粒 + top_limit = 0
    ]
    total = bad = 0
    for n, ms, lim in cases:
        xs = [rng.uniform(-50, 450) for _ in range(n)]
        ys = [rng.uniform(-100, 2400) for _ in range(n)]
        vys = [rng.uniform(-900, 60) for _ in range(n)]
        tls = [rng.uniform(0.0, 0.06) for _ in range(n)]
        # 边界注入
        if n >= 6:
            xs[0], ys[0], vys[0], tls[0] = 0.0, 0.0, -0.0, 0.0
            xs[1], vys[1], tls[1] = -0.0, 0.0, 1e-300
            vys[2], tls[2] = -1e-320, 0.05
            xs[3], ys[3] = float("nan"), float("nan")   # NaN 也不许分叉
            vys[4], tls[4] = 2.0 / ms * 1.0, 1.0        # trail 恰为 2 附近
            ys[5], vys[5], tls[5] = lim, 0.0, 0.0       # top 恰为 top_limit
        a = scalar_pack(xs, ys, vys, tls, ms, lim)
        b = vector_pack(xs, ys, vys, tls, ms, lim)
        total += n
        if a != b:
            bad += 1
            for i in range(n):
                if a[i * 12:(i + 1) * 12] != b[i * 12:(i + 1) * 12]:
                    print("  n=%d 首个不同颗粒 %d: %s vs %s"
                          % (n, i, a[i * 12:(i + 1) * 12].hex(),
                             b[i * 12:(i + 1) * 12].hex()))
                    break
    print("整段公式: %d 个样本, %d 组不一致" % (total, bad))
    return bad == 0


def check_direct_f32_block():
    """「直接建 `<f4` 块再逐列赋值」 == 「先建 f64 再 `astype('<f4')`」。

    2026-10-07 把打包从后者改成前者(少一次分配 + 少一趟遍历), 这条是那次改动的**唯一假设**。
    覆盖 0/-0/denormal/±float32 极值 —— 两条路都走 IEEE 就近舍入, 但必须**实测**而不是断言。
    """
    rng = random.Random(7)
    pool = [0.0, -0.0, 1e-320, -1e-320, 3.4e38, -3.4e38, 1.17e-38, 1.0, -1.0]
    bad = 0
    for _ in range(300):
        n = rng.randint(1, 60)
        vals = [rng.choice(pool) if rng.random() < 0.5
                else rng.uniform(-2.9e38, 2.9e38) for _ in range(n * 4)]
        blk64 = np.array(vals, dtype=np.float64).reshape(n, 4)
        ref = blk64.astype("<f4").tobytes()
        blk32 = np.empty((n, 4), dtype="<f4")
        for col in range(4):
            blk32[:, col] = blk64[:, col]
        if blk32.tobytes() != ref:
            bad += 1
    print("  直接建 f32 块 vs astype: %d/300 不一致" % bad)
    return bad == 0


def main():
    ok = check_float32_cast()
    ok = check_direct_f32_block() and ok
    ok = check_formula() and ok
    print("PASS: 向量化打包与逐颗粒打包字节完全相同" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
