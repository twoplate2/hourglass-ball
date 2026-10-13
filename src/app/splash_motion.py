"""v1.244 ballistic splashes and surface damping, with local jet reentry culling."""

import math

try:
    import numpy as np
except ImportError:
    np = None


def step(w, table, radius, inv, bottom, center, gravity, scale, pixel, jet,
         scalar=False):
    if scalar or np is None:
        return _scalar(w, table, radius, inv, bottom, center, gravity, jet)
    return _vector(w, table, radius, inv, bottom, center, gravity, jet)


def _limits(w):
    # These are the v1.244 surface response settings, not a new rolling model.
    return w._splash_limits


def _jet(jet, x, y, half):
    if jet is None:
        return np.zeros(len(x), dtype=bool), np.zeros(len(x), dtype=bool)
    low, high, inv, left, right = jet
    i = np.clip(((y - low) * inv).astype(np.intp), 0, len(left) - 1)
    valid = (y >= low) & (y <= high) & (left[i] <= right[i])
    return valid & (x >= left[i] - half) & (x <= right[i] + half), valid


def _vector(w, table, radius, inv, bottom, center, gravity, jet):
    n = w._sn
    sx, sy, vx, vy = w.sx[:n], w.sy[:n], w.svx[:n], w.svy[:n]
    rest, still, has, dt = w.srest[:n], w.sstill[:n], w.shas[:n], w.sdt[:n]
    min_vx, damp, rest_life, still_life, slope_gain = _limits(w)
    stopped = has != 0.0
    active = ~stopped
    still[stopped] += dt[stopped]
    keep = ~(stopped & (still > still_life))
    g = -gravity * w.sgd[:n]
    old_x, old_y = sx.copy(), sy.copy()
    y2 = sy + vy * dt + 0.5 * g * dt * dt
    vy2 = vy + g * dt
    x2 = sx + vx * dt
    sx[active], sy[active], vy[active] = x2[active], y2[active], vy2[active]
    dx = sx - w._cx
    out = (((np.abs(dx) + 1.0) ** 2 + (sy - center) ** 2) > radius ** 2) & active
    expired = np.zeros(n, dtype=bool)
    if table is not None:
        tab = np.asarray(table)
        z = (dx + radius) * inv
        i = np.trunc(z).astype(np.intp)
        k = np.clip(i, 0, len(tab) - 2)
        surf = bottom + tab[k] + (tab[k + 1] - tab[k]) * (z - k)
        surf = np.where(i < 0, bottom + tab[0],
                        np.where(i >= len(tab) - 1, bottom + tab[-1], surf))
        hit = active & ~out & (vy < 0.0) & (sy <= surf)
        hi = np.flatnonzero(hit)
        sy[hi], vy[hi] = surf[hi], 0.0
        rest[hi] += dt[hi]
        stop = hit & (np.abs(vx) < min_vx)
        si = np.flatnonzero(stop)
        vx[si], has[si] = 0.0, 1.0
        still[si] += dt[si]
        keep &= ~(stop & (still > still_life))
        slide = hit & ~stop
        ri = np.flatnonzero(slide)
        if slope_gain > 0.0:
            for p in ri:
                m = (w._mound_top_at(float(sx[p]) + 2.0)
                     - w._mound_top_at(float(sx[p]) - 2.0)) / 4.0
                vx[p] += (1.0 if dx[p] >= 0.0 else -1.0) * gravity * abs(m) / math.hypot(1, m) * slope_gain * dt[p]
        vx[ri] *= np.maximum(0.0, 1.0 - damp * dt[ri])
        expired = slide & (rest > rest_life)
    inside, valid = _jet(jet, sx, sy, w.shw[:n])
    mid_inside, _ = _jet(jet, 0.5 * (old_x + sx), 0.5 * (old_y + sy), w.shw[:n])
    free = active & (sy > bottom) & (vy != 0.0)
    reentry = free & (w.saway[:n] != 0.0) & (inside | mid_inside)
    near, _ = _jet(jet, sx, sy, w.shw[:n] + 0.1 * w._contact_flow_diameter())
    w.saway[:n][free & valid & ~near] = 1.0
    below = (sy < center - radius) & active
    for reason, mask in (("glass", out | below), ("reentered", reentry),
                         ("expired", expired)):
        dead = keep & mask
        w._splash_stats[reason] += int(np.count_nonzero(dead))
        keep &= ~dead
    w._splash_stats["settled"] += int(np.count_nonzero(~keep & stopped))
    w.srw[:n], w.srh[:n] = w.shw[:n], w.shh[:n]
    return keep


def _scalar(w, table, radius, inv, bottom, center, gravity, jet):
    min_vx, damp, rest_life, still_life, slope_gain = _limits(w)
    keep = [True] * w._sn
    for i in range(w._sn):
        dt = w.sdt[i]
        if w.shas[i]:
            w.sstill[i] += dt
            if w.sstill[i] > still_life:
                keep[i] = False
                w._splash_stats["settled"] += 1
            continue
        old_x, old_y = w.sx[i], w.sy[i]
        g = -gravity * w.sgd[i]
        x = old_x + w.svx[i] * dt
        y = old_y + w.svy[i] * dt + 0.5 * g * dt * dt
        vy = w.svy[i] + g * dt
        w.sx[i], w.sy[i], w.svy[i] = x, y, vy
        dx = x - w._cx
        reason = None
        if (abs(dx) + 1.0) ** 2 + (y - center) ** 2 > radius ** 2:
            reason = "glass"
        elif table is not None and vy < 0.0:
            z = (dx + radius) * inv
            k = int(z)
            surf = (bottom + table[0] if k < 0 else
                    bottom + table[-1] if k >= len(table) - 1 else
                    bottom + table[k] + (table[k + 1] - table[k]) * (z - k))
            if y <= surf:
                w.sy[i], w.svy[i] = surf, 0.0
                w.srest[i] += dt
                if abs(w.svx[i]) < min_vx:
                    w.svx[i], w.shas[i] = 0.0, 1.0
                    w.sstill[i] += dt
                    if w.sstill[i] > still_life:
                        reason = "settled"
                else:
                    if slope_gain > 0.0:
                        m = (w._mound_top_at(x + 2.0) - w._mound_top_at(x - 2.0)) / 4.0
                        w.svx[i] += (1.0 if dx >= 0 else -1.0) * gravity * abs(m) / math.hypot(1, m) * slope_gain * dt
                    w.svx[i] *= max(0.0, 1.0 - damp * dt)
                    if w.srest[i] > rest_life:
                        reason = "expired"
        if reason is None and jet is not None and w.svy[i] != 0.0:
            low, high, rate, left, right = jet
            def inside(xx, yy, half):
                if not low <= yy <= high:
                    return False, False
                row = min(len(left) - 1, max(0, int((yy - low) * rate)))
                valid = left[row] <= right[row]
                return valid and left[row] - half <= xx <= right[row] + half, valid
            in_jet, valid = inside(x, w.sy[i], w.shw[i])
            mid, _ = inside(0.5 * (old_x + x), 0.5 * (old_y + w.sy[i]), w.shw[i])
            near, _ = inside(x, w.sy[i], w.shw[i] + 0.1 * w._contact_flow_diameter())
            if w.saway[i] and (in_jet or mid):
                reason = "reentered"
            if valid and not near:
                w.saway[i] = 1.0
        if reason is None and w.sy[i] < center - radius:
            reason = "glass"
        if reason:
            keep[i] = False
            w._splash_stats[reason] += 1
        w.srw[i], w.srh[i] = w.shw[i], w.shh[i]
    return keep
