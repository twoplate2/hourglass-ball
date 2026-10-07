"""Low-energy hops and dissipative rolling on the shared mound contact table."""

import math

try:
    import numpy as np
except ImportError:
    np = None


def _height(table, x0, inv, bottom, x):
    z = max(0.0, min(len(table) - 1.0, (x - x0) * inv))
    i = min(int(z), len(table) - 2)
    return bottom + table[i] + (table[i + 1] - table[i]) * (z - i)


def _surface(table, x0, inv, bottom, x, hw, hh):
    y = _height(table, x0, inv, bottom, x)
    d = max(1.0 / inv, hw + hh)
    m = (_height(table, x0, inv, bottom, x + d)
         - _height(table, x0, inv, bottom, x - d)) / (2.0 * d)
    norm = math.sqrt(1.0 + m * m)
    radius = (abs(m) * hw + hh) / norm
    return y, m, norm, radius


def _surface_np(table, x0, inv, bottom, x, hw, hh):
    def height(xx):
        z = np.clip((xx - x0) * inv, 0.0, len(table) - 1.0)
        i = np.minimum(z.astype(np.intp), len(table) - 2)
        return bottom + table[i] + (table[i + 1] - table[i]) * (z - i)
    y = height(x)
    d = np.maximum(1.0 / inv, hw + hh)
    m = (height(x + d) - height(x - d)) / (2.0 * d)
    norm = np.sqrt(1.0 + m * m)
    radius = (np.abs(m) * hw + hh) / norm
    return y, m, norm, radius


def _jet_scalar(jet, x, y, half):
    if jet is None:
        return False, False
    low, high, inv, left, right = jet
    if not low <= y <= high:
        return False, False
    i = min(len(left) - 1, max(0, int((y - low) * inv)))
    valid = left[i] <= right[i]
    return valid and left[i] - half <= x <= right[i] + half, valid


def _jet_np(jet, x, y, half):
    if jet is None:
        return np.zeros(len(x), dtype=bool), np.zeros(len(x), dtype=bool)
    low, high, inv, left, right = jet
    i = np.clip(((y - low) * inv).astype(np.intp), 0, len(left) - 1)
    valid = (y >= low) & (y <= high) & (left[i] <= right[i])
    return valid & (x >= left[i] - half) & (x <= right[i] + half), valid


def step(w, table, radius, inv, bottom, center, gravity, scale, pixel, jet,
         scalar=False):
    """Mutate parallel arrays, return a stable keep mask. No random draws here."""
    if scalar or np is None:
        return _step_scalar(w, table, radius, inv, bottom, center, gravity,
                            scale, pixel, jet)
    return _step_np(w, np.asarray(table), radius, inv, bottom, center, gravity,
                    scale, pixel, jet)


def _step_np(w, table, radius, inv, bottom, center, gravity, scale, pixel, jet):
    n = w._sn
    x, y = w.sx[:n], w.sy[:n]
    vx, vy = w.svx[:n], w.svy[:n]
    roll, still = w.sslide[:n], w.shas[:n]
    side, dist, age = w.sside[:n], w.sdist[:n], w.sage[:n]
    hw, hh = w.shw[:n], w.shh[:n]
    g = gravity * w.sgd[:n]
    x0 = w._cx - radius
    diameter = w._contact_flow_diameter()
    gamma = w.sdamp[:n] * scale
    contact_activity = w._mound_flow_strength()
    fade_life, max_life = 0.12 / scale, 1.8 / scale
    count = max(1, math.ceil(float(np.max(w.sdt[:n])) * min(240.0, 120.0 * scale)))
    h = w.sdt[:n] / count
    keep = np.ones(n, dtype=bool)
    for _ in range(count):
        active = keep & (still == 0.0)
        age[keep] += h[keep]
        surf, m, norm, support = _surface_np(table, x0, inv, bottom, x, hw, hh)
        # A rising mound accepts overlapping old effects without another impact kick.
        buried = active & (y < surf + support * norm - 0.5 * pixel)
        roll[buried] = 1.0
        old_x, old_y = x.copy(), y.copy()
        roll_dt = h.copy()
        airborne = active & (roll == 0.0)
        ai = np.flatnonzero(airborne)
        if ai.size:
            dt = h[ai]
            xx = x[ai] + vx[ai] * dt
            yy = y[ai] + vy[ai] * dt - 0.5 * g[ai] * dt * dt
            vv = vy[ai] - g[ai] * dt
            sy, sm, sn, sr = _surface_np(table, x0, inv, bottom, xx, hw[ai], hh[ai])
            gap0 = (y[ai] - surf[ai]) / norm[ai] - support[ai]
            gap1 = (yy - sy) / sn - sr
            vn = (-sm * vx[ai] + vv) / sn
            hit = (gap1 <= 0.05 * pixel) & (vn < 0.0)
            x[ai], y[ai], vy[ai] = xx, yy, vv
            hi = ai[hit]
            if hi.size:
                fraction = np.clip(gap0[hit] / np.maximum(
                    1e-9, gap0[hit] - gap1[hit]), 0.0, 1.0)
                x[hi] = old_x[hi] + vx[hi] * h[hi] * fraction
                hs, hm, hn, hr = _surface_np(table, x0, inv, bottom, x[hi], hw[hi], hh[hi])
                impact_vy = vv[hit] + g[hi] * h[hi] * (1.0 - fraction)
                normal = np.maximum(0.0, (hm * vx[hi] - impact_vy) / hn) * 0.12
                tangent = np.maximum(0.0, side[hi] * (vx[hi] + hm * impact_vy) / hn) * 0.75
                w.sbounce[hi] += 1.0
                hop = ((normal * normal / np.maximum(1e-9, 2.0 * g[hi] / hn)
                        >= np.maximum(0.5 * pixel, 0.35 * hr))
                       & (w.sbounce[hi] <= 2.0))
                normal = np.where(hop, normal, 0.0)
                vx[hi] = (side[hi] * tangent - hm * normal) / hn
                vy[hi] = (side[hi] * hm * tangent + normal) / hn
                y[hi] = hs + (hr + 0.05 * pixel) * hn
                roll[hi] = ~hop
                roll_dt[hi] = h[hi] * (1.0 - fraction)
            dist[ai] += np.maximum(0.0, side[ai] * (x[ai] - old_x[ai]) * norm[ai])
        ri = np.flatnonzero(active & (roll != 0.0))
        if ri.size:
            rs, rm, rn, rr = _surface_np(table, x0, inv, bottom, x[ri], hw[ri], hh[ri])
            u = np.maximum(0.0, side[ri] * vx[ri] * rn)
            z = np.clip((dist[ri] / w.svariant[ri] - 0.8 * diameter)
                        / (2.0 * diameter), 0.0, 1.0)
            core = np.clip((np.abs(x[ri] - w._cx) / diameter - 0.35) / 0.55, 0.0, 1.0)
            core = 1.0 - core * core * (3.0 - 2.0 * core)
            near_mu = 0.30 - 0.14 * contact_activity * core
            mu = near_mu + (0.82 - near_mu) * z * z * (3.0 - 2.0 * z)
            a = g[ri] * (-side[ri] * rm - mu) / rn
            damp = gamma[ri]
            dt = roll_dt[ri].copy()
            stopping = (a < 0.0) & (u > 0.0)
            ts = np.full(len(ri), np.inf)
            ts[stopping] = np.log1p(damp[stopping] * u[stopping] / -a[stopping]) / damp[stopping]
            dt = np.minimum(dt, ts)
            decay = np.exp(-damp * dt)
            speed = np.maximum(0.0, u * decay + a / damp * (1.0 - decay))
            travel = np.maximum(0.0, (u - a / damp) * (1.0 - decay) / damp + a / damp * dt)
            x[ri] += side[ri] * travel / rn
            dist[ri] += travel
            rs, rm, rn, rr = _surface_np(table, x0, inv, bottom, x[ri], hw[ri], hh[ri])
            y[ri] = rs + (rr + 0.05 * pixel) * rn
            vx[ri] = side[ri] * speed / rn
            vy[ri] = rm * vx[ri]
            stopped = (speed < 2.0 * scale) & (a <= 0.0)
            pi = ri[stopped]
            still[pi] = 1.0
            vx[pi] = vy[pi] = 0.0
        si = np.flatnonzero(keep & (still != 0.0))
        if si.size:
            ss, sm, sn, sr = _surface_np(table, x0, inv, bottom, x[si], hw[si], hh[si])
            y[si] = ss + (sr + 0.05 * pixel) * sn
            w.sstill[si] += h[si]
        inside, valid = _jet_np(jet, x, y, hw)
        away = w.saway[:n]
        free = active & (roll == 0.0)
        reentry = free & (away != 0.0) & inside
        mid_inside, _ = _jet_np(jet, (old_x + x) * 0.5, (old_y + y) * 0.5, hw)
        reentry |= free & (away != 0.0) & mid_inside
        near_jet, _ = _jet_np(jet, x, y, hw + 0.1 * diameter)
        away[free & valid & ~near_jet] = 1.0
        out_ball = ((np.abs(x - w._cx) + hw) ** 2
                    + (np.abs(y - center) + hh) ** 2 > (radius + 0.1 * pixel) ** 2)
        settled = (still != 0.0) & (w.sstill[:n] >= fade_life)
        expired = age >= max_life + fade_life
        for reason, mask in (("reentered", reentry), ("glass", out_ball),
                             ("settled", settled), ("expired", expired)):
            dead = keep & mask
            w._splash_stats[reason] += int(np.count_nonzero(dead))
            keep &= ~dead
    fade = np.clip(np.maximum(w.sstill[:n] / fade_life,
                             (age - max_life) / fade_life), 0.0, 1.0)
    weight = 1.0 - fade * fade * (3.0 - 2.0 * fade)
    w.srw[:n] = hw * weight
    w.srh[:n] = hh * weight
    return keep


def _step_scalar(w, table, radius, inv, bottom, center, gravity, scale, pixel, jet):
    n = w._sn
    count = max(1, math.ceil(max(w.sdt[:n]) * min(240.0, 120.0 * scale)))
    x0, diameter = w._cx - radius, w._contact_flow_diameter()
    fade_life, max_life = 0.12 / scale, 1.8 / scale
    contact_activity = w._mound_flow_strength()
    keep = [True] * n
    for i in range(n):
        x, y, vx, vy = (float(getattr(w, name)[i]) for name in ("sx", "sy", "svx", "svy"))
        hw, hh, side = w.shw[i], w.shh[i], w.sside[i]
        g, h = gravity * w.sgd[i], w.sdt[i] / count
        gamma = w.sdamp[i] * scale
        for _ in range(count):
            w.sage[i] += h
            active = w.shas[i] == 0.0
            surf, m, norm, support = _surface(table, x0, inv, bottom, x, hw, hh)
            if active and y < surf + support * norm - 0.5 * pixel:
                w.sslide[i] = 1.0
            old_x, old_y = x, y
            roll_dt = h
            if active and w.sslide[i] == 0.0:
                xx, yy = x + vx * h, y + vy * h - 0.5 * g * h * h
                vv = vy - g * h
                sy, sm, sn, sr = _surface(table, x0, inv, bottom, xx, hw, hh)
                gap0, gap1 = (y - surf) / norm - support, (yy - sy) / sn - sr
                x, y, vy = xx, yy, vv
                if gap1 <= 0.05 * pixel and (-sm * vx + vv) / sn < 0.0:
                    fraction = min(1.0, max(0.0, gap0 / max(1e-9, gap0 - gap1)))
                    x = old_x + vx * h * fraction
                    hs, hm, hn, hr = _surface(table, x0, inv, bottom, x, hw, hh)
                    impact_vy = vv + g * h * (1.0 - fraction)
                    normal = max(0.0, (hm * vx - impact_vy) / hn) * 0.12
                    tangent = max(0.0, side * (vx + hm * impact_vy) / hn) * 0.75
                    w.sbounce[i] += 1.0
                    hop = (normal * normal / max(1e-9, 2.0 * g / hn)
                           >= max(0.5 * pixel, 0.35 * hr) and w.sbounce[i] <= 2.0)
                    normal = normal if hop else 0.0
                    vx, vy = (side * tangent - hm * normal) / hn, (side * hm * tangent + normal) / hn
                    y = hs + (hr + 0.05 * pixel) * hn
                    w.sslide[i] = 0.0 if hop else 1.0
                    roll_dt = h * (1.0 - fraction)
                w.sdist[i] += max(0.0, side * (x - old_x) * norm)
            if active and w.sslide[i] != 0.0:
                rs, rm, rn, rr = _surface(table, x0, inv, bottom, x, hw, hh)
                u = max(0.0, side * vx * rn)
                z = min(1.0, max(0.0, (w.sdist[i] / w.svariant[i] - 0.8 * diameter) / (2.0 * diameter)))
                core = min(1.0, max(0.0, (abs(x - w._cx) / diameter - 0.35) / 0.55))
                core = 1.0 - core * core * (3.0 - 2.0 * core)
                near_mu = 0.30 - 0.14 * contact_activity * core
                mu = near_mu + (0.82 - near_mu) * z * z * (3.0 - 2.0 * z)
                a, dt = g * (-side * rm - mu) / rn, roll_dt
                if a < 0.0 and u > 0.0:
                    dt = min(dt, math.log1p(gamma * u / -a) / gamma)
                decay = math.exp(-gamma * dt)
                speed = max(0.0, u * decay + a / gamma * (1.0 - decay))
                travel = max(0.0, (u - a / gamma) * (1.0 - decay) / gamma + a / gamma * dt)
                x += side * travel / rn
                w.sdist[i] += travel
                rs, rm, rn, rr = _surface(table, x0, inv, bottom, x, hw, hh)
                y, vx = rs + (rr + 0.05 * pixel) * rn, side * speed / rn
                vy = rm * vx
                if speed < 2.0 * scale and a <= 0.0:
                    w.shas[i], vx, vy = 1.0, 0.0, 0.0
            if w.shas[i]:
                ss, sm, sn, sr = _surface(table, x0, inv, bottom, x, hw, hh)
                y = ss + (sr + 0.05 * pixel) * sn
                w.sstill[i] += h
            inside, valid = _jet_scalar(jet, x, y, hw)
            free = active and w.sslide[i] == 0.0
            mid_inside, _ = _jet_scalar(jet, 0.5 * (old_x + x), 0.5 * (old_y + y), hw)
            reentry = free and w.saway[i] != 0.0 and (inside or mid_inside)
            near_jet, _ = _jet_scalar(jet, x, y, hw + 0.1 * diameter)
            if free and valid and not near_jet:
                w.saway[i] = 1.0
            reason = ("reentered" if reentry else
                      "glass" if (abs(x - w._cx) + hw) ** 2 + (abs(y - center) + hh) ** 2 > (radius + 0.1 * pixel) ** 2 else
                      "settled" if w.shas[i] and w.sstill[i] >= fade_life else
                      "expired" if w.sage[i] >= max_life + fade_life else None)
            if reason:
                keep[i] = False
                w._splash_stats[reason] += 1
                break
        w.sx[i], w.sy[i], w.svx[i], w.svy[i] = x, y, vx, vy
        fade = min(1.0, max(0.0, w.sstill[i] / fade_life, (w.sage[i] - max_life) / fade_life))
        weight = 1.0 - fade * fade * (3.0 - 2.0 * fade)
        w.srw[i], w.srh[i] = hw * weight, hh * weight
    return keep
