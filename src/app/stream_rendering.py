"""Shared stream drawing math; never changes particle motion or sand mass."""

DENSITY_PAD_PX = 20.0
CONTACT_REACH_PX = 20.0
EDGE_PERIOD = 1024.0
LOWER_DENSITY_START = 350.0
LOWER_DENSITY_GAIN = 0.012
LOWER_DENSITY_MAX = 6.0


def trail_length(vy, trail_time, motion_scale, trail_scale=1.0, tail_blend=0.0):
    trail = max(2.0, abs(vy) * trail_time / motion_scale * trail_scale)
    # Compensate only the faster lower grains; retain the existing upper stream.
    lower = min(LOWER_DENSITY_MAX, max(0.0, abs(vy) / motion_scale
                                     - LOWER_DENSITY_START) * LOWER_DENSITY_GAIN)
    return (trail + DENSITY_PAD_PX + lower) * (1.0 - tail_blend) + tail_blend


def trail_lengths(np, vys, trail_times, motion_scale, trail_scale=1.0, tail_blend=0.0):
    trail = np.abs(vys) * trail_times / motion_scale * trail_scale
    np.maximum(trail, 2.0, out=trail)
    trail += DENSITY_PAD_PX
    lower = np.maximum(np.abs(vys) / motion_scale - LOWER_DENSITY_START, 0.0)
    lower *= LOWER_DENSITY_GAIN
    np.minimum(lower, LOWER_DENSITY_MAX, out=lower)
    trail += lower
    trail *= 1.0 - tail_blend
    trail += tail_blend
    return trail


def contact_reach(np, ys, surfaces):
    """Only bridge an actual small contact gap, never extend the whole column."""
    gap = ys - surfaces
    return np.where((gap > 0.0) & (gap <= CONTACT_REACH_PX), gap, 0.0)


def edge_phase(elapsed, motion_scale, half_width):
    grain_px = max(1.0, min(4.0, half_width * 0.15))
    return (400.0 * max(0.25, motion_scale) * elapsed / grain_px) % EDGE_PERIOD
