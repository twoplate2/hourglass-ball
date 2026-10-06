"""沙流粒子的向量化物理内核(1.3 / numpy 路线)。

为什么需要它: MuMu 实测 2525 颗粒时 `update_particles` 占 5.79ms(整帧 35%),
每颗粒 ~2.3µs 全是 CPython 字节码 —— **瓶颈不在 GPU**(粒子全关掉帧仍有 8.61ms)。
把粒子全砍光帧也只有 9.7ms, 所以 +200% 必须连物理一起打掉。

⚠️ 逐位等价的三条硬规则(违反任何一条, 画面就会变):

1. **结合律必须照抄**。Python 的 `y += vy*dt + 0.5*g*dt*dt` 等价于
   `y + ((vy*dt) + (((0.5*g)*dt)*dt))`; numpy 里若写成 `py + pvy*pdt + 0.5*g*pdt*pdt`
   会按 `(py + (pvy*pdt)) + (...)` 求值 —— **差一个 ULP**。凡有多项相加, 一律显式加括号。

2. **只算 `[0:n]` 切片**。数组尾部是上一轮的残留值(可能是任意数), 整数组参与运算会
   把垃圾算进去(还可能触发 warning)。

3. **随机数不在这里抽**。命中事件的下标升序返回给调用方, 由调用方按原顺序回放
   `rand() < 0.25` → `rand() < 0.50` → `uniform ×2` → `choice`。

已实测(本机 numpy 2.2.2, 与 CPython 逐位对比): `sin` / 乘加 **0 ULP 差**。

⚠️ **但"`np.power(x, 0.5)` 与 `np.sqrt(x)` 等价"是错的**(2026-10-07 实测纠正)。
本条以前写的是"`x**0.5` / `sqrt` … 全部 0 ULP 差" —— 在 `step` 实际用到的量级
`x ∈ [3600, 7.2e5]` 上, 30 万组里 **166 组(0.055%)差 1 ULP**(相对差 2.2e-16 ≈ eps)。
numpy 的 `power` 走通用 `pow`, 与硬件 `sqrtsd` 的舍入在某些输入上不同。
⇒ **不要为了"看起来更快"把这里的 `np.power(..., 0.5)` 换成 `np.sqrt(...)`** ——
省约 0.03ms, 代价是 0.055% 的粒子每帧挪 1 ULP, 而那正是本项目"逐位等价"的红线。
(要换就先跑 `tools/test_physics_equiv.py` + `tools/inspect_flow.py` 的逐像素。)
"""

import numpy as np


def step(px, py, pvy, pxo, pwp, pwa, psz, pdt, n, c):
    """把 [0:n) 的粒子推进一帧。原地改 py/pvy/px。

    参数
    ----
    px, py, pvy : 每帧变化的字段(会被原地写回)
    pxo, pwp, pwa, psz : spawn 后不变的字段(只读)
    pdt : 本帧步长(只有本帧新生的粒子有偏值, 其余为 dt)
    n   : 存活粒子数
    c   : 常量字典, 见下面 keys()

    返回
    ----
    (hit_idx, peak_offset)
    hit_idx     : 命中(触底)粒子的下标, **升序**, 调用方据此回放随机数与删除
    peak_offset : 更新后的沙堆中心 EMA 偏移
    """
    sl = slice(0, n)
    pxv = px[sl]          # ⚠️ 参数名是 px(不是 x): 用于按 x 查接触高度曲线, 见下
    y = py[sl]
    vy = pvy[sl]
    dt = pdt[sl]

    g = c["g"]
    g_abs = c["g_abs"]
    mound_top = c["mound_top"]

    # ⚠️ 这两个**别名**就够了(原来写的是 `.copy()`, 每帧白拷 2×2750 个 float64 = 44KB):
    #    `y`/`vy` 下面都是**重绑定**(`y = y + ...` / `y = np.where(...)`), 不是原地改;
    #    而 `py[sl]`/`pvy[sl]` 直到函数末尾的 `py[sl] = y` 才被写 ⇒ 在这之前它们就是旧值。
    old_y = y
    old_vy = vy

    # y += vy*dt + 0.5*g*dt*dt —— 括号位置与原式一一对应(见模块头 规则 1)
    y = y + (vy * dt + 0.5 * g * dt * dt)
    vy = vy + g * dt

    # 接触高度按**上一帧的 x** 查 H(x)(专家 dingbu.md §7: 取消平台后不再只认一个 y)。
    # `y <= mound_top` 是必要非充分的免费预筛(锥顶是全堆最高点) ⇒ 只有落到堆附近的才查表。
    # ⚠️ 查找公式必须与 main.py 标量路径**逐位一致**: z<0 → cy[0]; z>=n1 → cy[-1];
    #    其余 i=int(z)(非负 ⇒ 截断), f=z-i, 线性插值。np.clip + minimum 复现同一规则。
    curve = c.get("curve")
    if curve is not None and len(curve[0]) > 1:
        _cx, _cy, _x0, _scale, _n1 = curve
        # 曲线由 main.py 以 Python list 传入(标量路径直接下标读) ⇒ 这里转一次 ndarray
        # 才能做花式索引。129 个 float 的拷贝, 每帧一次, 可忽略。
        _cy = np.asarray(_cy, dtype=np.float64)
        z = np.clip((pxv - _x0) * _scale, 0.0, _n1)
        idx = np.minimum(z.astype(np.intp), _n1 - 1)
        hy = _cy[idx] + (_cy[idx + 1] - _cy[idx]) * (z - idx)
        # ⚠️ 下面所有用到 mound_top 的**形状**项(近底喇叭口/落点判定)仍走标量参考高度 ——
        #    它只管流束外形, 不再负责碰撞(专家 §7.1)。
        hit = (y <= mound_top) & (y <= hy)
        hy_eff = np.where(y <= mound_top, hy, mound_top)
    else:
        hit = y <= mound_top
        hy_eff = mound_top
    if hit.any():
        d = old_y - hy_eff
        distance = np.maximum(d, 0.0)          # d if d > 0 else 0
        v = -old_vy
        speed = np.maximum(v, 0.0)             # v if v > 0 else 0
        denom = speed + np.sqrt(speed * speed + 2 * g_abs * distance)
        denom = np.where(denom > 1e-6, denom, 1e-6)
        hit_dt = 2 * distance / denom
        hit_dt = np.minimum(hit_dt, dt)        # hit_dt if hit_dt < step_dt else step_dt
        y = np.where(hit, hy_eff, y)
        vy = np.where(hit, old_vy + g * hit_dt, vy)

    fd = c["gen_y"] - y
    fallen_dist = np.maximum(fd, 0.0)          # fd if fd > 0 else 0

    # 管内填满(shrink=1), 出管后按流量守恒 A·v=常数收缩, 近底 30px 喇叭口微扩
    # ⚠️ 标量版用 `if y > lower_cut: shrink = 1.0` 跳过这一整段; 向量化会对全部粒子求值,
    # 而 lower_cut 以上的粒子 bottom_tube < 0 → 底数可能为负 → power 出 NaN + RuntimeWarning。
    # 底数夹到 1.0 只影响本来就被 np.where 丢弃的那批(y > lower_cut), 在用到的分支上
    # 底数恒 ≥ source_speed_squared(3600), 夹取不改变任何数值。
    below_tube = c["lower_cut"] - y
    v_at_y = np.power(np.maximum(c["source_speed_squared"] + 2 * g_abs * below_tube, 1.0), 0.5)
    target = np.power(c["source_speed"] / v_at_y, 0.5)
    # ⚠️ 下限从 `consts` 读(由 main.py 的 `FLOW_SHRINK_MIN` 传入) —— 两条路径**不可能**再各写各的
    _smin = c["shrink_min"]
    target = np.where(target <= _smin, _smin, target)
    shrink_body = np.where(below_tube < 40.0,
                           1.0 + (target - 1.0) * (below_tube / 40.0),
                           target)
    dist_to_floor = y - mound_top
    shrink_body = np.where((dist_to_floor > 0.0) & (dist_to_floor < 30.0),
                           shrink_body * (1 + (1 - dist_to_floor / 30.0) * 0.4),
                           shrink_body)
    shrink = np.where(y > c["lower_cut"], 1.0, shrink_body)

    cx = c["cx"]
    x = (cx + pxo[sl] * shrink
         + np.sin(fallen_dist * 0.07 + pwp[sl]) * pwa[sl] * (1 - shrink * 0.4))

    # 横向 clamp: 管内壁 / 进下球随球内壁过渡
    dy = y - c["lower_center"]
    r = c["Ri2"] - dy * dy
    raw_ball = np.where(r > 0.0, np.sqrt(np.maximum(r, 0.0)), 0.0)
    t = np.minimum((c["lower_top"] - y) / 30.0, 1.0)
    lim_ball = c["tube_lim"] + (raw_ball - c["tube_lim"]) * t
    lim = np.where(y >= c["lower_top"], c["tube_lim"], lim_ball)
    lim = lim - np.where(psz[sl] > 1, psz[sl], 0.5)     # half_stroke
    lim = np.maximum(lim, 0.0)
    x = cx + np.clip(x - cx, -lim, lim)

    px[sl] = x
    py[sl] = y
    pvy[sl] = vy

    if not hit.any():
        return _EMPTY, _EMPTY, c["peak_offset"]

    # 命中粒子的沙堆中心 EMA —— 顺序必须按下标升序, 与原标量循环一致
    peak_offset = c["peak_offset"]
    if mound_top > c["lower_bot"] + 1:
        for xv in x[hit]:
            peak_offset = peak_offset * 0.97 + (xv - cx) * 0.03
    # hit_dt 必须原样带出去: 调用方要用 `step_dt - hit_dt` 作为 splash 的 _step_dt,
    # 从 post-hit 的 vy 反推会差 ULP。
    return np.nonzero(hit)[0], hit_dt[hit], peak_offset


_EMPTY = np.empty(0, dtype=np.intp)
