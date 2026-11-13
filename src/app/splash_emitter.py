"""Batch dense contact bursts without changing grain count or random streams.

Geometry and trigonometry retain the scalar emitter's arithmetic. Only shared
parameters and array writes are batched; sparse contacts keep the scalar path.
"""
import math
import numpy as np


def replay(w, hit_idx, hit_dt, settings):
    w._sand_landed += float(np.sum(w.pmass[hit_idx]))
    profile, apex = w._mound_profile, w._mound_apex()
    contact = profile.contact if profile is not None and apex > 0.0 else None
    rand = settings['random'].random
    pending = []
    effect_rng = None
    rows = zip(w.px[hit_idx].tolist(), w.pvy[hit_idx].tolist(),
               w.pdt[hit_idx].tolist(), np.asarray(hit_dt).tolist())
    for x, vy, dt, hit_time in rows:
        first = w._record_contact(x, -vy)
        if rand() < 0.25 * w._splash_density:
            height = w._lower_sand_bot + (contact(x-w._cx, apex) if contact else 0.0)
            w.flares.append({'x': x, 'y': height, 'end': w._mound_flow_clock + 0.08})
        selected = rand() < settings['SPLASH_HIT_CHANCE'] * w._splash_density
        if selected or first:
            # Clone the effect RNG at exactly the original first-emission point,
            # before any subsequent main-RNG draws (including first-hit extras).
            if effect_rng is None:
                effect_rng = w._splash_random()
            left = max(0.0, dt-hit_time)
            pending.append((x, -vy, left))
            if first:
                for _ in range(w._first_contact_extra(rand)):
                    pending.append((x, -vy, left))
    if pending:
        _launch(w, pending, effect_rng, profile, apex, settings)


def _discard(rng, size_mix):
    rng.random()
    rng.choice((-1.0, 1.0))
    rng.random()
    rng.choice(size_mix)
    rng.random()


def _launch(w, pending, rng, profile, apex, s):
    start, cap = w._sn, w._splash_cap
    sizes = s['SPLASH_SIZE_MIX']
    if cap and start >= cap:
        for _ in pending:
            _discard(rng, sizes)
        w._splash_stats['pool_full'] += len(pending)
        return
    lo = min(s['SPLASH_ANGLE_MIN'], s['SPLASH_ANGLE_MAX'])
    hi = max(s['SPLASH_ANGLE_MIN'], s['SPLASH_ANGLE_MAX'])
    lo = min(math.pi/2, max(math.pi/2-s['SPLASH_HORIZONTAL_ANGLE_LIMIT'], lo))
    hi = min(math.pi/2, max(lo, hi))
    px = s['SPLASH_PX_BASE'] * max(1.0, w._R_inner/140.0)
    size_map = {item: tuple(round(k*px) for k in item) for item in sizes}
    uniform, choice = rng.uniform, rng.choice
    rows = []
    for x, impact, dt in pending:
        if cap and start + len(rows) >= cap:
            _discard(rng, sizes)
            w._splash_stats['pool_full'] += 1
            continue
        gain = uniform(s['SPLASH_SPEED_LO'], s['SPLASH_SPEED_HI']) * s['SPLASH_GAIN']
        side = choice((-1.0, 1.0))
        angle = uniform(lo, hi)
        size = size_map[choice(sizes)]
        gd = uniform(s['SPLASH_GRAV_LO'], s['SPLASH_GRAV_HI'])
        if profile is None or impact <= 0.0:
            continue
        height, free, thick = profile.column(x-w._cx, apex)
        if not free and thick > 0.0:
            continue
        # Use libm scalar sin/cos as before, not an approximate vector kernel.
        rows.append((x, impact, dt, gain, side, math.sin(angle), abs(math.cos(angle)),
                     size[0]*.5, size[1]*.5, gd, w._lower_sand_bot+height,
                     w._lower_sand_bot+profile.bounds(x-w._cx)[1]))
    if not rows:
        return
    x, impact, dt, gain, side, sine, cosine, hw, hh, gd, surface, roof = np.array(rows).T
    diameter = w._contact_flow_diameter()
    side = np.where(np.abs(x-w._cx) >= .1*diameter, np.where(x>w._cx, 1., -1.), side)
    reference = w._splash_reference_speed or impact
    strength = reference * (.90 + .10*np.minimum(1.0, impact/reference))
    speed = strength * gain * w._splash_speed_scale
    vx, vy = side*sine*speed, cosine*speed
    scale = w._particle_motion_scale
    power = s['SPLASH_G_SCALE']
    gravity = (450.0*(scale*scale if power >= 1.0 else scale**power))*gd
    wanted = hh + (s['SPLASH_LIFT_PX']-hh)*w._splash_origin_blend
    lift = np.minimum(np.maximum(0.0, wanted), np.maximum(0.0, roof-surface-hh))
    clearance = np.maximum(0.0, roof-surface-lift-hh)
    vy = np.minimum(vy, np.sqrt(2.0*gravity*clearance))
    count = len(rows)
    end = start+count
    w._s_grow(end)
    values = dict(sx=x, sy=surface+lift, svx=vx, svy=vy, shw=hw, shh=hh,
                  sgd=gd, srw=hw, srh=hh, sdt=dt)
    for name, data in values.items():
        getattr(w, name)[start:end] = data
    for name in ('srest', 'sstill', 'shas', 'saway', 'stag'):
        getattr(w, name)[start:end] = 0.0
    w._sn = end
    w._splash_stats['born_air'] += count
