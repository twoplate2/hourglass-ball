"""Shared geometry, grain-motion and appearance calculations for the hourglass."""

import math


def first_pile_height(height, age, mouth):
    """Ease the shared visible and contact surface after the first real impact."""
    if height <= 0 or age >= .45:
        return height
    phase = max(0., age) / .45
    blend = phase * phase * (3. - 2. * phase)
    early = mouth * (.22 + 7. * max(0., age))
    return height - max(0., height - early) * (1. - blend)


def coarse_width_parameters(half, pitch, original_floor, original_ramp):
    """Jet contraction length, minimum width, and smooth lip transition."""
    mouth = max(1., 2. * half)
    # Let the jet contract along the fall. Retain room for a few grains in
    # the smallest aperture without imposing that floor on a tablet-sized jet.
    floor = min(original_floor, max(.38, 3.3 * pitch / max(1., half)))
    return 2.25 * mouth, floor, min(original_ramp, .55 * mouth)


def neck_release_motion(half, stem_height):
    """Length, exit speed and transit time for the last part of the filled neck."""
    length = max(0., min(stem_height, 1.35 * half))
    inlet = 47.5
    if length == 0.:
        return 0., inlet, 0.
    # Acceleration increases linearly from zero at entry to gravity at exit.
    # At distance x, v² = inlet² + (k*x)². All values use unscaled game time.
    k = math.sqrt(450. / length)
    return length, math.hypot(inlet, k * length), math.asinh(k * length / inlet) / k


def tail_duration(clearance, motion_scale, transfer_duration):
    """Give the last cohorts time to separate before crossing the final free gap."""
    gap = max(1., clearance)
    flight = 2. * gap / (47.5 + math.sqrt(47.5**2 + 900. * gap)) / motion_scale
    return min(.20 * transfer_duration, 1.24 * flight)


def tail_emission_time(remaining, span):
    """Integral of a steady particle rate that falls quadratically to zero."""
    if remaining >= span:
        return remaining - 2. * span / 3.
    return max(0., remaining)**3 / (3. * span * span)


def tail_remaining_time(budget, span):
    """Recover the exact remaining time at a grain's birth from that integral."""
    if budget >= span / 3.:
        return budget + 2. * span / 3.
    return (max(0., budget) * 3. * span * span)**(1. / 3.)


def drained_stream_y(xs, ys, *, center, outlet, half, pitch, supplied):
    """Round only the exposed rear edge of an already exhausted stream.

    Translate complete grain sprites; no opacity fade, deletion, new grains
    or independent timer. The effect grows from the actual empty outlet gap.
    """
    import numpy as np
    y = np.asarray(ys)
    if len(y) == 0 or supplied < 1.0-1e-12:
        return y
    top = float(np.max(y))
    gap = max(0.0, outlet-top)
    # Landing lower grains must not lift/flatten the surviving rear cohort.
    # This is the silhouette of the whole rear cap, not a micro-grain ripple.
    amplitude = min(.60*half, 2.5*gap)
    if amplitude <= 0.0:
        return y
    depth = np.clip((top-y)/(2.5*amplitude),0.,1.)
    cap = 1.0-depth*depth*(3.0-2.0*depth)
    radial = np.minimum(1.0,np.abs(np.asarray(xs)-center)/max(1.0,half))
    return y-amplitude*cap*(.15+.85*radial*radial)


def stream_texture_phase(elapsed, motion_scale, diameter):
    """One repeating texture period, advanced by the application's elapsed time."""
    return (400.0 * max(0.25, motion_scale) * elapsed / max(2.0, diameter)) % 1.0


def contact_surface_profile(offset, diameter, thickness, slope):
    """Return the local surface depth and bounded outward texture displacement."""
    diameter = max(2.0, diameter)
    u = abs(offset) / (2.0 * diameter)
    if thickness <= 0.0 or u >= 1.0:
        return 0.0, 0.0, 0.0
    weight = 1.0 - u * u * (3.0 - 2.0 * u)
    depth = min(thickness, 0.16 * diameter * weight)
    direction = max(-1.0, min(1.0, offset / (0.3 * diameter)))
    direction = direction * (1.5 - 0.5 * direction * direction)
    distance = 0.28 * diameter * weight
    dx = direction * distance / math.sqrt(1.0 + slope * slope)
    return depth, dx, slope * dx


def receiving_surface_profile(offset,diameter,thickness,slope,clearance):
    """A confined surface still receives sand, with very small local motion."""
    depth,dx,dy=contact_surface_profile(offset,diameter,thickness,slope)
    t=max(0.0,min(1.0,clearance/max(1e-6,.35*diameter)))
    t=t*t*(3-2*t)
    depth*=.5+.5*t
    dx*=.5+.5*t
    dy*=.5+.5*t
    return depth,dx,dy
