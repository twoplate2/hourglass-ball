"""Small, deterministic rendering controls; no particle or timer state is changed."""

import math


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
