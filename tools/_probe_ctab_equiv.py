"""接触表: `contact()` 逐点调用 vs **保序内联**, **逐位等价守卫** + 桌面计时对照。

⚠️ 这不是"函数级微基准能不能外推"的问题 —— 我**只**用它回答"同一路径内哪段更贵"
(README 的教训是: 既有的 `_NUMPY_MIN=800` 拆门当初就是被函数级微基准坑的,
因为真实循环里有每帧新建临时对象的分配成本)。这里没有分配、没有临时对象,
两个写法**逐位等价**(由 `tools/_splash_golden.py` 金标准轨迹兜底), 所以差值就是净值。
"""
import math, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as m

N = m.CONTACT_TABLE_N
# 造一个真实的 _MoundProfile(形状用主程序同款生成路径拿不到, 这里直接构造一个合法形状)
shape = tuple(math.sin(math.pi * i / 64.0) * 6.0 for i in range(65))
prof = m._MoundProfile(radius=164.0, shape=shape)
apex = 37.0
r = prof.radius
inv = (N - 1) / (2.0 * r)
tab = [0.0] * N
ROUNDS = 300


def build_call():
    c = prof.contact
    for k in range(N):
        tab[k] = c(-r + k / inv, apex)


def build_inline():
    rr = prof.radius
    shp = prof.shape
    sn = len(shp) - 1
    two_r = 2.0 * rr
    r2 = rr * rr
    for k in range(N):
        dx = -rr + k / inv
        x = dx if dx > -rr else -rr
        if x > rr:
            x = rr
        d2 = r2 - x * x
        half = math.sqrt(d2) if d2 > 0.0 else 0.0
        floor = rr - half
        roof = rr + half
        z = (dx + rr) / two_r * sn
        if z <= 0.0:
            off = shp[0]
        elif z >= sn:
            off = shp[-1]
        else:
            i = int(z)
            off = shp[i] + (shp[i + 1] - shp[i]) * (z - i)
        p = apex + off
        tab[k] = floor if p < floor else (roof if p > roof else p)


def build_precomputed():
    # 真正的生产路径: 几何部分只建一次, 每帧只做加法 + 钳位
    prof.fill_contact_table(tab, -r, inv, apex)


for fn, name in ((build_call, "contact() 逐点 "), (build_inline, "本地保序内联"),
                 (build_precomputed, "生产:预计算  ")):
    fn()
    t = time.perf_counter()
    for _ in range(ROUNDS):
        fn()
    dt = (time.perf_counter() - t) / ROUNDS * 1000.0
    print("%s  %7.4f ms / %d 点 = %.3f us/点" % (name, dt, N, dt * 1000 / N))

# ---- 逐位等价守卫: 多组随机 (radius, shape, apex) ----
import random as _rnd
_rnd.seed(20261007)
bad = 0
for case in range(200):
    r_ = _rnd.uniform(20.0, 400.0)
    n_ = _rnd.choice((5, 9, 17, 33, 65, 129))
    shp_ = tuple(_rnd.uniform(-r_ * 0.2, r_ * 0.2) for _ in range(n_))
    if not any(abs(v) > 1e-9 for v in shp_):
        shp_ = (0.0,) * (n_ - 1) + (1.0,)
    ap_ = _rnd.uniform(0.0, r_ * 1.2)
    pr = m._MoundProfile(radius=r_, shape=shp_)
    iv = (N - 1) / (2.0 * r_)
    a = [0.0] * N
    for k in range(N):
        a[k] = pr.contact(-r_ + k / iv, ap_)
    b = [0.0] * N
    pr.fill_contact_table(b, -r_, iv, ap_)
    for k in range(N):
        if a[k] != b[k]:
            bad += 1
            if bad <= 3:
                print("!! 第 %d 组 第 %d 点: contact=%.17g  inline=%.17g" % (case, k, a[k], b[k]))
            break
print("逐位等价守卫: 200 组随机用例, 不一致 %d 组" % bad)
