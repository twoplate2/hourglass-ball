"""Shared geometry, grain-motion and appearance calculations for the hourglass."""

import math
from functools import lru_cache
from typing import NamedTuple

B13_REFERENCE_RADIUS = 208.845
B13_LIP_SPEED = 21.0
B13_SPEED_SCATTER = 3.0
B13_SPEED_GAIN = 200.0
B13_RAMP_SECONDS = 3.0
# All apertures carry visible pigment at the same speed (417.69 px cup).
B13_TEXTURE_SPEED = 80.


def _unit_interval(x):
    return x.clip(0., 1.) if hasattr(x, "clip") else max(0., min(1., x))


def b13_depth(age, launch_speed):
    """Original B13 fall retained for splash timing."""
    u = _unit_interval(age / B13_RAMP_SECONDS)
    ramp_distance = B13_RAMP_SECONDS * u**3 * (1. - .5 * u)
    coast_time = age - B13_RAMP_SECONDS * u
    return launch_speed * age + B13_SPEED_GAIN * (ramp_distance + coast_time)


def b13_flight(distance, launch_speed):
    if distance <= 0.:
        return 0.
    lo, hi = 0., distance / launch_speed
    for _ in range(26):
        age = .5 * (lo + hi)
        if b13_depth(age, launch_speed) < distance:
            lo = age
        else:
            hi = age
    return hi


@lru_cache(maxsize=32)
def b13_first_flight(distance, radius):
    return b13_flight(distance * B13_REFERENCE_RADIUS / radius, B13_LIP_SPEED + B13_SPEED_SCATTER)


class B13Transit(NamedTuple):
    """Bulk travel time from the lip, using the exposed jet's packed volume / flow.

    Coordinates use the reference glass. The occupied section falls from a
    nearly filled mouth to a narrower porous stream over the first 54 pixels.
    The integral of its relative area is .72*z + .28*z/(1 + z/54).
    """

    seconds_per_pixel: float

    @classmethod
    @lru_cache(maxsize=32)
    def for_glass(cls, duration, half):
        radius = B13_REFERENCE_RADIUS
        return cls(.96 * .75 / radius * duration * (half / radius)**2)

    @staticmethod
    def lane_factor(lane, jitter):
        return 1. - .08 * lane**2 + .02 * jitter

    def age_at(self, depth):
        return self.seconds_per_pixel * depth * (.72 + .28 / (1. + depth / 54.))

    def depth_at(self, age):
        x = age / (54. * self.seconds_per_pixel)
        return 54. * (x - 1. + ((1. - x)**2 + 2.88*x)**.5) / 1.44

    @staticmethod
    def relative_area(depth):
        return .72 + .28 / (1. + depth / 54.)**2

    def motion(self, age, lane, jitter, outlet, scale):
        factor = self.lane_factor(lane, jitter)
        depth = self.depth_at(age * factor)
        speed = factor / (self.seconds_per_pixel * self.relative_area(depth))
        return outlet - scale * depth, -scale * speed

    def cohorts(self, start, end, clock, floor_depth, duration, rate):
        """Group the volume released in [start, end] into dated collision samples."""
        # A late UI tick can cover minutes. Group already floor-reaching sand
        # separately, preserving normal sampling of the part still in flight.
        oldest_live = clock - self.age_at(floor_depth + 2.) / self.lane_factor(1., -1.)
        if end - start > .25 and oldest_live > start:
            stop = min(oldest_live, end)
            yield .5 * (start + stop), (stop - start) / duration
            start = stop
        if start < end:
            count = max(1, math.ceil(rate * (end - start)))
            step = (end - start) / count
            for i in range(count):
                yield start + (i + .5) * step, step / duration


@lru_cache(maxsize=32)
def _opening_transit(duration, half):
    from opening_transit import OpeningTransit
    capacity = B13Transit.for_glass(duration, half)
    return OpeningTransit(duration, capacity) if duration > 5. else capacity


def hourglass_transit(duration, half, clock=0.):
    """One shipping profile for the screen, contact physics and all APK builds."""
    opening = _opening_transit(duration, half)
    return opening.at_clock(clock) if duration > 5. else opening


def b13_neck_ratio(height, half):
    # For the last shallow layer, width and height shrink together. With both
    # scaled by this cube root the remaining volume still follows the ledger.
    return min(1., (max(0., height) / max(1., .85 * half)) ** (1. / 3.))


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
