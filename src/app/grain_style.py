"""Small, deterministic rendering controls; no particle or timer state is changed."""

import math


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
