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


def _surface_step(w, table, radius, inv, bottom, center, gravity, scalar=False):
    """Keep landed grains on the slope until they settle; never lift buried grains.

    srest already records the first surface contact. Reusing it preserves the
    emitter, particle identities and random stream without another particle pool.
    A genuinely receding surface releases the grain to the existing ballistic
    path. The usual small downhill displacement follows the same contact table.
    """
    n=w._sn
    min_vx,damp,rest_life,still_life,slope_gain=_limits(w)
    if scalar or np is None:
        handled,keep=[False]*n,[True]*n
        if table is None:return handled,keep
        def height(x):
            z=(x-w._cx+radius)*inv;k=int(z)
            return (bottom+table[0] if k<0 else bottom+table[-1] if k>=len(table)-1
                    else bottom+table[k]+(table[k+1]-table[k])*(z-k))
        for i in range(n):
            if w.srest[i]<=0.0 or w.shas[i]:continue
            dt=w.sdt[i]
            if dt<=0.0:handled[i]=True;continue
            old_x,old_y=w.sx[i],w.sy[i]
            old_surface=height(old_x)
            half=max(.1,w.shh[i])
            if old_surface<old_y-half:continue
            handled[i]=True
            if old_surface>old_y+half:
                w.svx[i]=w.svy[i]=0.0;w.shas[i]=1.0
                w.sstill[i]+=dt
            else:
                x=old_x+w.svx[i]*dt;y=height(x)
                w.sx[i],w.sy[i],w.svy[i]=x,y,0.0
                w.srest[i]+=dt
                if abs(w.svx[i])<min_vx:
                    w.svx[i]=0.0;w.shas[i]=1.0;w.sstill[i]+=dt
                else:
                    if slope_gain>0.0:
                        m=(height(x+2.0)-height(x-2.0))/4.0
                        w.svx[i]+=(1.0 if x>=w._cx else -1.0)*gravity*abs(m)/math.hypot(1,m)*slope_gain*dt
                    w.svx[i]*=max(0.0,1.0-damp*dt)
            if (abs(w.sx[i]-w._cx)+1.0)**2+(w.sy[i]-center)**2>radius**2:
                reason='glass'
            elif w.shas[i] and w.sstill[i]>still_life:reason='settled'
            elif not w.shas[i] and w.srest[i]>rest_life:reason='expired'
            else:reason=None
            if reason:keep[i]=False;w._splash_stats[reason]+=1
            w.srw[i],w.srh[i]=w.shw[i],w.shh[i]
        return handled,keep
    handled=np.zeros(n,dtype=bool);keep=np.ones(n,dtype=bool)
    if table is None:return handled,keep
    eligible=(w.srest[:n]>0.0)&(w.shas[:n]==0.0)
    handled[eligible&(w.sdt[:n]<=0.0)]=True
    ids=np.flatnonzero(eligible&(w.sdt[:n]>0.0))
    if not len(ids):return handled,keep
    tab=np.asarray(table)
    def height(x):
        z=(x-w._cx+radius)*inv;k=np.trunc(z).astype(np.intp);j=np.clip(k,0,len(tab)-2)
        y=bottom+tab[j]+(tab[j+1]-tab[j])*(z-j)
        return np.where(k<0,bottom+tab[0],np.where(k>=len(tab)-1,bottom+tab[-1],y))
    old_y=w.sy[ids].copy();old_surface=height(w.sx[ids]);half=np.maximum(.1,w.shh[ids])
    supported=old_surface>=old_y-half
    ids,old_y,old_surface,half=[a[supported] for a in (ids,old_y,old_surface,half)]
    handled[ids]=True
    buried=old_surface>old_y+half
    bi,ri=ids[buried],ids[~buried]
    w.svx[bi],w.svy[bi],w.shas[bi]=0.0,0.0,1.0
    w.sstill[bi]+=w.sdt[bi]
    w.sx[ri]+=w.svx[ri]*w.sdt[ri]
    w.sy[ri],w.svy[ri]=height(w.sx[ri]),0.0
    w.srest[ri]+=w.sdt[ri]
    stop=np.abs(w.svx[ri])<min_vx
    si,mi=ri[stop],ri[~stop]
    w.svx[si],w.shas[si]=0.0,1.0
    w.sstill[si]+=w.sdt[si]
    if slope_gain>0.0:
        m=(height(w.sx[mi]+2.0)-height(w.sx[mi]-2.0))/4.0
        w.svx[mi]+=np.where(w.sx[mi]>=w._cx,1.0,-1.0)*gravity*np.abs(m)/np.hypot(1,m)*slope_gain*w.sdt[mi]
    w.svx[mi]*=np.maximum(0.0,1.0-damp*w.sdt[mi])
    outside=(np.abs(w.sx[ids]-w._cx)+1.0)**2+(w.sy[ids]-center)**2>radius**2
    stopped=w.shas[ids]!=0.0
    for reason,dead in [('glass',outside),('settled',stopped&(w.sstill[ids]>still_life)),
                        ('expired',~stopped&(w.srest[ids]>rest_life))]:
        actual=ids[dead&keep[ids]];keep[actual]=False
        w._splash_stats[reason]+=len(actual)
    w.srw[ids],w.srh[ids]=w.shw[ids],w.shh[ids]
    return handled,keep


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
    surface,surface_keep=_surface_step(w,table,radius,inv,bottom,center,gravity)
    active &= ~surface
    keep &= surface_keep
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
    surface,surface_keep=_surface_step(w,table,radius,inv,bottom,center,gravity,scalar=True)
    for i in range(w._sn):
        if surface[i]:
            keep[i]=surface_keep[i]
            continue
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
