"""量粒子这一步**端到端**的净收益: 老管线(main 分支形态) vs 过渡态 vs 现在。

三条管线渲染出的东西一样, 差别只在数据访问方式:

| 管线 | 物理 | 每帧形状转换 | 渲染读端 |
|---|---|---|---|
| 旧(主线 main 分支) | 在 dict 列表上算 | 无(物理就在 dict 上) | 读 dict |
| 过渡态(上一提交) | 向量化 | 数组 -> ~2500 个 dict | 读 dict |
| 现在 | 向量化 | `arr[:n].tolist()` 摊成 list | 按下标读 list |

过渡态那一步实测比省下来的还多(净 -20%), 这就是本次把读端全部改成下标的原因。
本基准把三条管线都摆出来, 免得只报"向量化内核快了 3.7x"那种骗人的数字。

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


def make_dict_population(px, py, pvy, pxo, pwp, pwa, psz, ptl, pli):
    """老形态的粒子字典列表(与 main 分支 spawn 的字段一致)。"""
    return [{
        "x": px[i], "x_offset": pxo[i], "y": py[i], "vy": pvy[i],
        "wobble_phase": pwp[i], "wobble_amp": pwa[i], "size": int(psz[i]),
        "trail_time": ptl[i], "is_light": bool(pli[i]),
    } for i in range(len(py))]


def sync_dicts(px, py, pvy, pxo, pwp, pwa, psz, ptl, pli, pn):
    """过渡态那层兼容层(数组 -> dict 列表) —— 本次已删除, 只作对照。"""
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


def ref_step_dicts(particles, dt, c):
    """main 分支 `update_particles` 粒子循环的逐字镜像(只在原地改, **不移除**命中)。

    计时用: 每条重复调用都跑同一批 dict, 不把建 dict / 移除的代价摊进来。
    `_step_dt` 用 get(真实帧里除帧内新生外都不带这个键)。
    """
    cx = c["cx"]
    mound_top = c["mound_top"]
    g = c["g"]
    g_abs = c["g_abs"]
    source_speed = c["source_speed"]
    source_speed_squared = c["source_speed_squared"]
    lower_cut = c["lower_cut"]
    lower_top = c["lower_top"]
    lower_center = c["lower_center"]
    tube_lim = c["tube_lim"]
    Ri2 = c["Ri2"]
    gen_y = c["gen_y"]
    sin = math.sin
    sqrt = math.sqrt
    for p in particles:
        step_dt = p.get("_step_dt", dt)
        y = p["y"]
        vy = p["vy"]
        old_y, old_vy = y, vy
        x_offset = p["x_offset"]
        wobble_phase = p["wobble_phase"]
        wobble_amp = p["wobble_amp"]
        size = p["size"]
        y += vy * step_dt + 0.5 * g * step_dt * step_dt
        vy += g * step_dt
        hit = y <= mound_top
        if hit:
            d = old_y - mound_top
            distance = d if d > 0 else 0
            v = -old_vy
            speed = v if v > 0 else 0
            denom = speed + sqrt(speed * speed + 2 * g_abs * distance)
            hit_dt = 2 * distance / (denom if denom > 1e-6 else 1e-6)
            hit_dt = hit_dt if hit_dt < step_dt else step_dt
            y = mound_top
            vy = old_vy + g * hit_dt
        fd = gen_y - y
        fallen_dist = fd if fd > 0.0 else 0.0
        if y > lower_cut:
            shrink = 1.0
        else:
            below_tube = lower_cut - y
            v_at_y = (source_speed_squared + 2 * g_abs * below_tube) ** 0.5
            target = (source_speed / v_at_y) ** 0.5
            if target <= 0.70:
                target = 0.70
            if below_tube < 40.0:
                shrink = 1.0 + (target - 1.0) * (below_tube / 40.0)
            else:
                shrink = target
            dist_to_floor = y - mound_top
            if 0 < dist_to_floor < 30:
                shrink *= 1 + (1 - dist_to_floor / 30) * 0.4
        x = cx + x_offset * shrink + sin(fallen_dist * 0.07 + wobble_phase) \
            * wobble_amp * (1 - shrink * 0.4)
        if y >= lower_top:
            lim = tube_lim
        else:
            dy = y - lower_center
            r = Ri2 - dy ** 2
            raw_ball = sqrt(r) if r > 0.0 else 0.0
            below = lower_top - y
            t = below / 30.0
            if t > 1.0:
                t = 1.0
            lim = tube_lim + (raw_ball - tube_lim) * t
        half_stroke = size if size > 1 else 0.5
        lim = lim - half_stroke
        if lim <= 0.0:
            lim = 0.0
        off = x - cx
        if off > lim:
            off = lim
        elif off < -lim:
            off = -lim
        x = cx + off
        # 命中在真实代码里是 `continue`(粒子被移除); 计时镜像不移除, 否则重复调用会越跑越空。
        p["y"] = y
        p["vy"] = vy
        p["x"] = x


# ---- 读端(与 main.py:_p_refresh_view / _group_stream_particles / 纹理打包 同形) ----
N_COLORS = 11
NECK_Y = 390.0
GLASS_BOT = 40.0
OUTLET = NECK_Y - 20.0
TONE_SCALE = 5 / math.tau


def refresh_view(px, py, pvy, ptl, psz, pli, pwp, pn):
    """main.py:_p_refresh_view 的同形实现(一次 C 循环摊成原生 float)。"""
    return (px[:pn].tolist(), py[:pn].tolist(), pvy[:pn].tolist(), ptl[:pn].tolist(),
            psz[:pn].tolist(), pli[:pn].tolist(), pwp[:pn].tolist())


def make_buckets():
    return {(index, size): []
            for index in list(range(N_COLORS)) + [-1] for size in (1, 2)}


def group_indices(view):
    """main.py:_group_stream_particles 的同形实现 —— 返回 {key: [下标]}。"""
    buckets = make_buckets()
    for bucket in buckets.values():
        bucket.clear()
    div = max(1.0, NECK_Y - GLASS_BOT) / N_COLORS
    by_key = [[buckets[(i, s)] for s in (1, 2)]
              for i in list(range(N_COLORS)) + [-1]]
    light_row = by_key[N_COLORS]
    last = N_COLORS - 1
    _x, ys, _vy, _tl, sizes, lights, phases = view
    for i in range(len(ys)):
        y = ys[i]
        if y >= OUTLET:
            continue
        if lights[i]:
            row = light_row
        else:
            w = int(phases[i] * TONE_SCALE)
            if w > 4:
                w = 4
            index = int((NECK_Y - y) / div) + w - 2
            if index < 0:
                index = 0
            elif index > last:
                index = last
            row = by_key[index]
        row[0 if sizes[i] == 1 else 1].append(i)
    return buckets


FLOAT3 = struct.Struct("<3f")


def pack_endpoints(view, buckets, top_limit, motion_scale, n):
    """TextureFlowBatch.update 的打包内循环同形实现(每颗粒一次 pack_into)。"""
    xs, ys, vys, trails, _sz, _li, _wp = view
    data = bytearray(((n // 512) + 1) * 512 * 3 * 4)
    pack = FLOAT3.pack_into
    for indices in buckets.values():
        offset = 0
        for k in indices:
            bottom = ys[k]
            vy = vys[k]
            if vy < 0:
                vy = -vy
            trail = vy * trails[k] / motion_scale
            if trail < 2:
                trail = 2
            top = bottom + trail
            if top > top_limit:
                top = top_limit
            pack(data, offset, xs[k], bottom, top)
            offset += 12
    return data


def group_dicts(particles):
    """main 分支 `_group_stream_particles` 的同形实现 —— 返 {key: [dict, ...]}。"""
    buckets = make_buckets()
    div = max(1.0, NECK_Y - GLASS_BOT) / N_COLORS
    by_key = [[buckets[(i, s)] for s in (1, 2)]
              for i in list(range(N_COLORS)) + [-1]]
    light_row = by_key[N_COLORS]
    last = N_COLORS - 1
    for p in particles:
        y = p["y"]
        if y >= OUTLET:
            continue
        if p["is_light"]:
            row = light_row
        else:
            w = int(p["wobble_phase"] * TONE_SCALE)
            if w > 4:
                w = 4
            index = int((NECK_Y - y) / div) + w - 2
            if index < 0:
                index = 0
            elif index > last:
                index = last
            row = by_key[index]
        row[0 if p["size"] == 1 else 1].append(p)
    return buckets


def pack_dicts(buckets, top_limit, motion_scale, n):
    """过渡态以前 TextureFlowBatch.update 的打包内循环(逐 dict 读)。"""
    data = bytearray(((n // 512) + 1) * 512 * 3 * 4)
    pack = FLOAT3.pack_into
    for particles in buckets.values():
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

    # 分组/打包单独计时时喂同一份快照, 避免把刷新耗时算进去。
    sample_view = refresh_view(npx, npy, npvy, nptl, npsz, npli, npwp, n)
    sample_buckets = group_indices(sample_view)
    dict_population = make_dict_population(px, py, pvy, pxo, pwp, pwa, psz, ptl, pli)
    dict_buckets = None

    def read_side():
        v = refresh_view(npx, npy, npvy, nptl, npsz, npli, npwp, n)
        pack_endpoints(v, group_indices(v), OUTLET, 1.0, n)

    def read_side_dicts():
        pack_dicts(group_dicts(dict_population), OUTLET, 1.0, n)

    def net():
        vector_only()
        read_side()

    def old_pipeline():
        ref_step_dicts(dict_population, dt, c)
        read_side_dicts()

    def transition_pipeline():
        vector_only()
        sync_dicts(npx, npy, npvy, npxo, npwp, npwa, npsz, nptl, npli, n)
        read_side_dicts()

    # 预热(镜像会原地改 dict, 先跑一轮把分布推近稳态)
    for _ in range(20):
        ref_step_dicts(dict_population, dt, c)

    t_scalar = timeit(scalar, REPEAT)
    t_vec = timeit(vector_only, REPEAT)
    t_sync = timeit(compat, REPEAT)
    t_view = timeit(lambda: refresh_view(npx, npy, npvy, nptl, npsz, npli, npwp, n), REPEAT)
    t_group = timeit(lambda: group_indices(sample_view), REPEAT)
    t_pack = timeit(lambda: pack_endpoints(sample_view, sample_buckets, OUTLET, 1.0, n),
                    REPEAT)
    t_read = timeit(read_side, REPEAT)
    t_net = timeit(net, REPEAT)
    t_dict_phys = timeit(lambda: ref_step_dicts(dict_population, dt, c), REPEAT)
    t_read_old = timeit(read_side_dicts, REPEAT)
    t_old = timeit(old_pipeline, REPEAT)
    t_trans = timeit(transition_pipeline, REPEAT)

    print("颗粒数 n = %d,   每档取 %d 次里最快的一次" % (n, REPEAT))
    print("  ---- 物理 ------------------------------------------------")
    print("  旧: 在 dict 上算(主线)  %8.3f ms/帧" % (t_dict_phys * 1e3))
    print("  过渡: 向量化            %8.3f ms/帧" % (t_vec * 1e3))
    print("  现在: 向量化            %8.3f ms/帧" % (t_vec * 1e3))
    print("  ---- 每帧形状转换 ----------------------------------------")
    print("  过渡: 数组 -> dict       %8.3f ms/帧(本次删除)" % (t_sync * 1e3))
    print("  现在: tolist×7           %8.3f ms/帧" % (t_view * 1e3))
    print("  ---- 渲染读端(分组 + 打包) -------------------------------")
    print("  旧: 读 dict              %8.3f ms/帧" % (t_read_old * 1e3))
    print("  现在: 按下标读 list      %8.3f ms/帧   (分组 %.3f + 打包 %.3f)"
          % (t_read * 1e3, t_group * 1e3, t_pack * 1e3))
    print("  ---- 端到端(物理 + 转换 + 读端) ---------------------------")
    print("  旧(主线 main 分支)       %8.3f ms/帧" % (t_old * 1e3))
    print("  过渡态(上一提交)         %8.3f ms/帧   %.2fx" % (t_trans * 1e3, t_old / t_trans))
    print("  现在                     %8.3f ms/帧   %.2fx" % (t_net * 1e3, t_old / t_net))
    print()
    print("  通量: dict 物理 %.0f 颗粒/ms   向量化 %.0f 颗粒/ms"
          % (n / (t_dict_phys * 1e3), n / (t_vec * 1e3)))
    print("  参考: 列表式标量算术 %8.3f ms/帧(不含 dict 读写)" % (t_scalar * 1e3))


if __name__ == "__main__":
    main()
