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
print("建表逐位等价守卫: 200 组随机用例, 不一致 %d 组" % bad)

# ---- 同时守卫 `geometry_at` 缓存路径: `column`/`contact`/`has_sand`/`free_surface`
#      必须与"不走缓存"的参考实现逐位相同(缓存命中返回的是同一个 float, 但**首次未命中**
#      那条路径也要逐位正确)。
def _ref_bounds(pf, dx):
    r = pf.radius
    x = min(max(dx, -r), r)
    half = math.sqrt(max(0.0, r * r - x * x))
    return r - half, r + half


def _ref_shape_at(pf, dx):
    r = pf.radius
    shp = pf.shape
    n = len(shp)
    z = (dx + r) / (2.0 * r) * (n - 1)
    if z <= 0.0:
        return shp[0]
    if z >= n - 1:
        return shp[-1]
    i = int(z)
    return shp[i] + (shp[i + 1] - shp[i]) * (z - i)


bad2 = 0
for case in range(300):
    r_ = _rnd.uniform(20.0, 400.0)
    n_ = _rnd.choice((5, 9, 17, 33, 65))
    shp_ = tuple(_rnd.uniform(-r_ * 0.2, r_ * 0.2) for _ in range(n_))
    if not any(abs(v) > 1e-9 for v in shp_):
        shp_ = (0.0,) * (n_ - 1) + (1.0,)
    pr = m._MoundProfile(radius=r_, shape=shp_)
    ap_ = _rnd.uniform(0.0, r_ * 1.2)
    for _ in range(12):
        dx = _rnd.uniform(-r_ * 1.5, r_ * 1.5)
        floor, roof = _ref_bounds(pr, dx)
        p = ap_ + _ref_shape_at(pr, dx)
        want_y = floor if p < floor else (roof if p > roof else p)
        want_col = (want_y, floor < p < roof, want_y - floor)
        want_con = want_y
        want_has = p > floor
        want_free = floor < p < roof
        if (pr.column(dx, ap_) != want_col or pr.contact(dx, ap_) != want_con
                or pr.has_sand(dx, ap_) != want_has
                or pr.free_surface(dx, ap_) != want_free):
            bad2 += 1
            if bad2 <= 3:
                print("!! dx=%.17g apex=%.17g 处不一致" % (dx, ap_))
            break
print("geometry_at 缓存路径守卫: 300 组 x 12 点, 不一致 %d 组" % bad2)
bad += bad2

# ---- `contact_np`: 一批 dx 一次算完 —— 必须与逐点 `contact()` **逐位相同**
#      (`_replay_hits` 用它替掉每颗一次的 `_mound_contact_h`, 那里占 0.183ms/帧)
import numpy as _np
bad3 = 0
for case in range(300):
    r_ = _rnd.uniform(20.0, 400.0)
    n_ = _rnd.choice((5, 9, 17, 33, 65))
    shp_ = tuple(_rnd.uniform(-r_ * 0.2, r_ * 0.2) for _ in range(n_))
    if not any(abs(v) > 1e-9 for v in shp_):
        shp_ = (0.0,) * (n_ - 1) + (1.0,)
    pr = m._MoundProfile(radius=r_, shape=shp_)
    ap_ = _rnd.uniform(0.0, r_ * 1.2)
    # 覆盖: 球内 / 越界 / 恰在 ±r / 恰在控制点 / 随机
    dxs = [_rnd.uniform(-r_ * 1.6, r_ * 1.6) for _ in range(40)]
    dxs += [-r_, r_, 0.0, -r_ * 0.999, r_ * 0.999]
    dxs += [(-r_ + 2.0 * r_ * i / (n_ - 1)) for i in range(n_)]
    got = pr.contact_np(_np.asarray(dxs, dtype=_np.float64), ap_)
    for j, dx in enumerate(dxs):
        want = pr.contact(dx, ap_)
        if float(got[j]) != want:
            bad3 += 1
            if bad3 <= 3:
                print("!! contact_np 第 %d 组 第 %d 点: dx=%.17g np=%.17g scalar=%.17g"
                      % (case, j, dx, float(got[j]), want))
            break
print("contact_np 守卫: 300 组 x %d 点, 不一致 %d 组" % (len(dxs), bad3))
bad += bad3
