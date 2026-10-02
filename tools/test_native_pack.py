"""native/flowcore.pack_stream 与 Python 参考实现的逐字节等价测试。

判据: 随机数据(含 0/1/5/64/512/1500 颗粒、负 vy、极小 trail、超 top_limit 截断、
不同 motion_scale)下两份 bytearray **逐字节相同**。改 flowcore.c 后必须先跑这个。
"""
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "native"))
import flowcore  # noqa: E402

FLOAT3 = struct.Struct("<3f")


def py_pack(particles, data, top_limit, motion_scale):
    pack = FLOAT3.pack_into
    offset = 0
    for particle in particles:
        bottom = particle["y"]
        vy = particle["vy"]
        if vy < 0:
            vy = -vy
        trail = vy * particle["trail_time"] / motion_scale
        if trail < 2:
            trail = 2
        top = bottom + trail
        if top > top_limit:
            top = top_limit
        pack(data, offset, particle["x"], bottom, top)
        offset += 12
    return data


def main():
    random.seed(23)
    bad = 0
    for _ in range(200):
        n = random.choice([0, 1, 5, 64, 512, 1500])
        particles = [{"x": random.uniform(-300, 900), "y": random.uniform(0, 2400),
                      "vy": random.uniform(-900, 900),
                      "trail_time": random.uniform(0.0, 0.05),
                      "size": random.choice([1, 2])} for _ in range(n)]
        top_limit = random.choice([100.0, 380.0, 712.0, 5000.0])
        scale = random.choice([1.0, 1.5, 3.2])
        size = max(12, n * 12) * 2
        left, right = bytearray(size), bytearray(size)
        py_pack(particles, left, top_limit, scale)
        flowcore.pack_stream(particles, right, top_limit, scale)
        if left != right:
            bad += 1
    print(f"pack_stream 逐字节等价: {'PASS' if bad == 0 else 'FAIL'} (不等 {bad}/200 组)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
