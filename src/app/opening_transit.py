"""Travel of the leading edge, calibrated in the 540 × 924 reference glass."""

from bisect import bisect_right
from functools import lru_cache
from math import ceil

from grain_style import B13Transit


# The last two free frames of the 30 fps film advance by 20 pixels.
CONTACT_SPEED = 600.


@lru_cache(maxsize=1)
def _reference_samples():
    # Measured visible tip in the v3.19 film, in reference pixels below the lip.
    # Frame 45 was still in flight; frame 46 contains the first deposit.
    outlet = 477.2729717005271
    floor = 909.9411099930996
    keys = [(0., 0.), (7/30, 1.5)]
    for frame, y in ((8, 482), (12, 495), (16, 515), (20, 544), (24, 580), (28, 625),
                     (32, 677), (36, 738), (40, 806), (44, 883), (45, 903)):
        keys.append((frame / 30., y - outlet))
    keys.append((1.5 + (floor - 903.) / CONTACT_SPEED, floor - outlet))

    # Monotone cubic interpolation preserves the measured positions and joins
    # their speeds smoothly. Tabulate both axes once for the inverse query used
    # by the visible grains, the collision model and the post-deadline tail.
    intervals = []
    secants = []
    for (t0, d0), (t1, d1) in zip(keys, keys[1:]):
        intervals.append(t1 - t0)
        secants.append((d1 - d0) / (t1 - t0))
    speeds = [4.]
    for i in range(1, len(intervals)):
        left = 2. * intervals[i] + intervals[i-1]
        right = intervals[i] + 2. * intervals[i-1]
        speeds.append((left + right) / (left / secants[i-1] + right / secants[i]))
    speeds.append(secants[-1])

    samples = []
    for i, dt in enumerate(intervals):
        t0, d0 = keys[i]
        v0, v1 = speeds[i:i+2]
        a = (3. * secants[i] - 2. * v0 - v1) / dt
        b = (v0 + v1 - 2. * secants[i]) / dt**2
        steps = ceil(dt * 240.)
        for k in range(steps):
            t = dt * k / steps
            samples.append((t0 + t, d0 + t * (v0 + t * (a + b*t)), v0 + t * (2.*a + 3.*b*t)))
    samples.append((*keys[-1], speeds[-1]))
    return samples


def _interpolate(value, xs, ys, final_slope):
    if getattr(value, 'ndim', 0):
        import numpy as np
        return np.interp(value, xs, ys) + np.maximum(0., value - xs[-1]) * final_slope
    if value <= xs[0]:
        return ys[0]
    if value >= xs[-1]:
        return ys[-1] + (value - xs[-1]) * final_slope
    hi = bisect_right(xs, value)
    lo = hi - 1
    u = (value - xs[lo]) / (xs[hi] - xs[lo])
    return ys[lo] + u * (ys[hi] - ys[lo])


class OpeningTransit:
    """Blend the capacity-limited short clocks into the measured reference drop."""

    # Both paths use the same dated volumes for collision and catch-up ticks.
    cohorts = B13Transit.cohorts

    def __init__(self, duration, short_transit):
        blend = min(1., max(0., (duration - 5.) / 25.))
        self.blend = blend**2 * (3. - 2. * blend)
        self.short_transit = short_transit
        self.times, self.depths, self.speeds = [], [], []
        self.reference_times = []
        for time, depth, speed in _reference_samples():
            self.times.append((1. - self.blend) * short_transit.age_at(depth) + self.blend * time)
            self.reference_times.append(time)
            self.depths.append(depth)
            dt_per_pixel = short_transit.seconds_per_pixel * short_transit.relative_area(depth)
            self.speeds.append(1. / ((1. - self.blend) * dt_per_pixel + self.blend / speed))
        # Preserve the approved 5.5 s opening. Short intermediate timers need
        # enough room to reach their steady speed before supply stops.
        self.warm_start = min(5.5, .40 * duration)
        self.warm_span = min(5., .35 * duration)
        self.steady_lead = self.table_age_at(172.)
        self.steady = FlowingTransit(self, self.steady_lead, 0.)

    def at_clock(self, clock):
        if clock <= self.warm_start:
            return self
        u = (clock - self.warm_start) / self.warm_span
        if u >= 1.:
            return self.steady
        lead = self.steady_lead * u**2 * (3. - 2. * u)
        lead_rate = self.steady_lead * 6. * u * (1. - u) / self.warm_span
        return FlowingTransit(self, lead, lead_rate)

    def lane_factor(self, lane, jitter, *, birth=None):
        short = self.short_transit.lane_factor(lane, jitter)
        reference = 1. - .03 * lane**2 - .005 * (1. - jitter)
        return short + self.blend * (reference - short)

    def age_at(self, depth):
        if self.blend == 1.:
            return self.table_age_at(depth)
        floor = self.depths[-1]
        bounded = depth.clip(None, floor) if getattr(depth, 'ndim', 0) else min(depth, floor)
        reference = _interpolate(bounded, self.depths, self.reference_times, 1. / CONTACT_SPEED)
        time = (1. - self.blend) * self.short_transit.age_at(bounded) + self.blend * reference
        return time + (depth - bounded) / self.speeds[-1]

    def table_age_at(self, depth):
        return _interpolate(depth, self.depths, self.times, 1. / self.speeds[-1])

    def depth_at(self, age):
        return _interpolate(age, self.times, self.depths, self.speeds[-1])

    def speed_at(self, age):
        return _interpolate(age, self.times, self.speeds, 0.)

    def motion(self, age, lane, jitter, outlet, scale, *, birth=None):
        factor = self.lane_factor(lane, jitter)
        t = age * factor
        return outlet - scale * self.depth_at(t), -scale * factor * self.speed_at(t)


class FlowingTransit:
    """Start at the established flow speed, using the same cached reference curve."""

    cohorts = B13Transit.cohorts
    __slots__ = ('curve', 'lead', 'lead_rate', 'start_depth', 'start_speed')

    def __init__(self, curve, lead, lead_rate):
        self.curve, self.lead, self.lead_rate = curve, lead, lead_rate
        self.start_depth = curve.depth_at(lead)
        self.start_speed = curve.speed_at(lead)

    def lane_factor(self, lane, jitter, *, birth=None):
        return self.curve.lane_factor(lane, jitter)

    def age_at(self, depth):
        return self.curve.table_age_at(self.start_depth + depth) - self.lead

    def depth_at(self, age):
        return self.curve.depth_at(self.lead + age) - self.start_depth

    def motion(self, age, lane, jitter, outlet, scale, *, birth=None):
        factor = self.lane_factor(lane, jitter)
        scaled_age = age * factor
        v = self.curve.speed_at(self.lead + scaled_age)
        # Both the cohort age and the established inlet speed advance during
        # warm-up. The second term is zero once the inlet is fully flowing.
        speed = v * factor + self.lead_rate * (v - self.start_speed)
        return outlet - scale * self.depth_at(scaled_age), -scale * speed
