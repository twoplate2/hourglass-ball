"""验证 tools/flow_numpy.step 与 main.py 原标量循环**逐位等价**。

手法: 把 main.py `update_particles` 里的粒子循环体逐字抄成标量参考实现, 用同一份
随机初值跑同一个场景, 每帧对 y / vy / x 做 double 的逐位比较(不是"近似相等")。

随机数不参与 —— 本测试只覆盖纯算术部分; 随机数的顺序由 main.py 侧的命中回放保证。

用法: python tools/test_physics_equiv.py
"""

import math
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

import flow_numpy


def bits(v):
    return struct.unpack("<q", struct.pack("<d", v))[0]


# ---------------------------------------------------------------- 标量参考实现
def ref_step(px, py, pvy, pxo, pwp, pwa, psz, pdt, dt, c):
    """逐字抄自 main.py:update_particles 的 `for p in self.particles:` 循环体。"""
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
    lower_bot = c["lower_bot"]
    gen_y = c["gen_y"]
    peak_offset = c["peak_offset"]
    jet_top = c["jet_top"]
    jet_vena = c["jet_vena"]
    jet_diffuse = c["jet_diffuse"]
    jet_spread = c["jet_spread"]
    jet_edge = c["jet_edge"]
    jet_wave_k = c["jet_wave_k"]

    out = {k: [] for k in ("x", "y", "vy", "xo", "wp", "wa", "sz")}
    hit_idx = []
    hit_dt_list = []
    n = len(py)
    for i in range(n):
        step_dt = pdt[i]
        y = py[i]
        vy = pvy[i]
        old_y, old_vy = y, vy
        x_offset = pxo[i]
        wobble_phase = pwp[i]
        wobble_amp = pwa[i]
        size = psz[i]
        y += vy * step_dt + 0.5 * g * step_dt * step_dt
        vy += g * step_dt
        hit = y <= mound_top
        hit_dt = 0.0
        if hit:
            d = old_y - mound_top
            distance = d if d > 0 else 0
            v = -old_vy
            speed = v if v > 0 else 0
            denom = speed + math.sqrt(speed * speed + 2 * g_abs * distance)
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
        x = cx + x_offset * shrink + math.sin(fallen_dist * 0.07 + wobble_phase) \
            * wobble_amp * (1 - shrink * 0.4)

        if y >= lower_top:
            lim = tube_lim
        else:
            dy = y - lower_center
            r = Ri2 - dy ** 2
            raw_ball = math.sqrt(r) if r > 0.0 else 0.0
            below = lower_top - y
            t = below / 30.0
            if t > 1.0:
                t = 1.0
            lim = tube_lim + (raw_ball - tube_lim) * t
        if y < jet_top:
            u = (jet_top - y) / jet_spread
            if u > 1.0:
                u = 1.0
            env = tube_lim * (jet_vena + (jet_diffuse - jet_vena) * u)
            _d = jet_top - y
            env += math.sin(_d * jet_wave_k) * tube_lim * jet_edge
            if env < lim:
                lim = env
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

        if hit:
            if mound_top > lower_bot + 1:
                peak_offset = peak_offset * 0.97 + (x - cx) * 0.03
            hit_idx.append(i)
            hit_dt_list.append(hit_dt)
            continue
        out["x"].append(x)
        out["y"].append(y)
        out["vy"].append(vy)
        out["xo"].append(x_offset)
        out["wp"].append(wobble_phase)
        out["wa"].append(wobble_amp)
        out["sz"].append(size)
    return out, hit_idx, hit_dt_list, peak_offset


def main():
    random.seed(23)
    np.random.seed(23)

    # 覆盖全部状态的场景: 管内 / 过渡区 / 自由落体 / 近底喇叭口 / 触底
    lower_bot = 40.0
    c = {
        "cx": 200.0,
        "gen_y": 420.0,
        "mound_top": 70.0,
        "g": -450.0,
        "g_abs": 450.0,
        "source_speed": 60.0,
        "source_speed_squared": 3600.0,
        "lower_cut": 300.0,
        "lower_top": 260.0,
        "lower_center": 150.0,
        "Ri2": 160.0 ** 2,
        "tube_lim": 6.0,
        "lower_bot": lower_bot,
        "peak_offset": 0.0,
        # 射流包络(与 main.py 的 JET_* 同值; 两处实现必须一起改)
        "jet_top": 300.0, "jet_vena": 0.68, "jet_diffuse": 1.00, "jet_spread": 110.0, "jet_edge": 0.30, "jet_wave_k": 0.42,
    }
    N = 4000
    px = [c["cx"] + random.uniform(-6, 6) for _ in range(N)]
    py = [random.uniform(lower_bot - 20, c["gen_y"]) for _ in range(N)]
    pvy = [random.uniform(-500.0, 60.0) for _ in range(N)]
    pxo = [random.uniform(-6.0, 6.0) for _ in range(N)]
    pwp = [random.uniform(0, math.tau) for _ in range(N)]
    pwa = [random.uniform(0.4, 1.0) for _ in range(N)]
    psz = [2.0 if random.random() < 0.85 else 1.0 for _ in range(N)]

    dt = 0.016
    # ⚠️ 必须混合步长: 真实工况里帧内新生的粒子带"偏步长"(0 ~ dt), 其余用整帧 dt。
    #    全程固定 dt 会漏掉这一类 —— 上一版测试就是这么放过 bug 的。
    pdt = [dt] * N

    # 同一份初值喂给向量化版本
    npx = np.array(px, dtype=np.float64)
    npy = np.array(py, dtype=np.float64)
    npvy = np.array(pvy, dtype=np.float64)
    npxo = np.array(pxo, dtype=np.float64)
    npwp = np.array(pwp, dtype=np.float64)
    npwa = np.array(pwa, dtype=np.float64)
    npsz = np.array(psz, dtype=np.float64)
    npdt = np.array(pdt, dtype=np.float64)

    bad = 0
    frames = 0
    total_hits = 0
    while len(py) > 0 and frames < 200:
        frames += 1
        ref, hit_idx, ref_hitdt, ref_peak = ref_step(
            px, py, pvy, pxo, pwp, pwa, psz, pdt, dt, c)

        n = len(py)
        c["peak_offset"] = c["peak_offset"]           # 两边各自演进
        nc = dict(c)
        nc["peak_offset"] = c["peak_offset"]
        n_hit, n_hitdt, n_peak = flow_numpy.step(
            npx[:n], npy[:n], npvy[:n], npxo[:n], npwp[:n], npwa[:n],
            npsz[:n], npdt[:n], n, nc)

        # 1) 命中集合必须一致
        if list(n_hit) != hit_idx:
            print("帧 %d: 命中下标不一致 ref=%s mine=%s"
                  % (frames, hit_idx[:10], list(n_hit)[:10]))
            bad += 1
            break
        total_hits += len(hit_idx)
        for a, b in zip(ref_hitdt, n_hitdt):
            if bits(a) != bits(b):
                print("帧 %d: hit_dt 逐位不一致 %r vs %r" % (frames, a, b))
                bad += 1
                break
        if bad:
            break

        # 2) peak_offset 必须逐位一致
        if bits(ref_peak) != bits(n_peak):
            print("帧 %d: peak_offset 不一致 %r vs %r" % (frames, ref_peak, n_peak))
            bad += 1
            break

        # 3) 存活粒子的 y / vy / x 必须逐位一致
        keep = np.ones(n, dtype=bool)
        keep[n_hit] = False
        for name, ref_list, mine in (("y", ref["y"], npy[:n][keep]),
                                     ("vy", ref["vy"], npvy[:n][keep]),
                                     ("x", ref["x"], npx[:n][keep])):
            for a, b in zip(ref_list, mine):
                if bits(a) != bits(b):
                    print("帧 %d: %s 逐位不一致 %r vs %r" % (frames, name, a, b))
                    bad += 1
                    break
            if bad:
                break
        if bad:
            break

        # 推进到下一帧
        c["peak_offset"] = ref_peak
        py, pvy, px = ref["y"], ref["vy"], ref["x"]
        pxo, pwp, pwa, psz = ref["xo"], ref["wp"], ref["wa"], ref["sz"]
        # 每帧随机让最后 k 颗粒子带偏步长(模拟帧内新生)
        pdt = [dt] * len(py)
        k = random.randint(0, 6)
        for j in range(max(0, len(py) - k), len(py)):
            pdt[j] = random.uniform(0.0, dt)
        kp = keep
        npy, npvy, npx = npy[:n][kp].copy(), npvy[:n][kp].copy(), npx[:n][kp].copy()
        npxo, npwp, npwa = npxo[:n][kp].copy(), npwp[:n][kp].copy(), npwa[:n][kp].copy()
        npsz = npsz[:n][kp].copy()
        npdt = np.array(pdt, dtype=np.float64)

    print("跑了 %d 帧, 累计命中 %d 次, 存活 %d" % (frames, total_hits, len(py)))
    if bad == 0:
        print("PASS: 标量版与向量化版逐位一致")
    else:
        print("FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
