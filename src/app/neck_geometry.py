"""Period-to-aperture geometry, measured inside the glass wall."""
import math
from bisect import bisect_right

MAX_RATIO=.39
FIVE_SECOND_RATIO=.28
MIN_RATIO=.015
WIDE_PERIOD=5.0
NARROW_PERIOD=36000.0


def body_metrics(width,height,min_radius=10.0):
    wall=max(2.0,width*6.0/380.0)
    radius=max(min_radius,min(width*.44,(height-height*.055-2*height*.002)/4))
    return radius,wall


def inner_width(period,diameter):
    if not math.isfinite(diameter) or diameter<=0:
        raise ValueError('A positive finite body diameter is required')
    if not math.isfinite(period) or period<=0:
        raise ValueError('A positive finite duration is required')
    if period < WIDE_PERIOD:
        u = math.log(max(1., period)) / math.log(WIDE_PERIOD)
        return diameter * MAX_RATIO * (FIVE_SECOND_RATIO / MAX_RATIO)**u
    # In the short clocks the actual opening must hold the first airborne
    # charge. From twenty seconds the same flight can carry less volume by
    # reducing area in proportion to flow. Keep long thin streams legible.
    normal_ratio = .24
    if period < 20.:
        u = math.log(period / WIDE_PERIOD) / math.log(20. / WIDE_PERIOD)
        return diameter * FIVE_SECOND_RATIO * (normal_ratio / FIVE_SECOND_RATIO)**u
    readable = .15 * (MIN_RATIO / .15)**min(1., math.log(period / WIDE_PERIOD)
                                               / math.log(NARROW_PERIOD / WIDE_PERIOD))
    return diameter * max(readable, normal_ratio * math.sqrt(20. / period))


def outer_half(period,diameter,wall):
    return inner_width(period,diameter)*.5+wall


def half_limits(diameter,wall):
    return diameter*MIN_RATIO*.5+wall,diameter*MAX_RATIO*.5+wall


class SandNeck:
    """Account for the spherical cap and the charge that fills the open neck."""

    def __init__(self, radius, pole, taper, transit, scale):
        self.radius = radius
        self.pole = pole
        self.outlet = taper[-1][1]
        self.heights = tuple(y for _, y in reversed(taper))
        self.radii = tuple(x for x, _ in reversed(taper))
        self.volumes = [0.]
        for a, b, h, k in zip(self.radii, self.radii[1:], self.heights, self.heights[1:]):
            self.volumes.append(self.volumes[-1] + (k-h)*(a*a+a*b+b*b)/3.)
        self.capacity = 4.*radius**3/3.
        cap_height = self.heights[-1] - pole
        self.cap_fraction = cap_height**2*(radius-cap_height/3.) / self.capacity
        self.full_fraction = self.volumes[-1] / self.capacity
        # In sphere-volume units the real full vessel replaces the bottom
        # spherical cap with the measured taper down to the lip.
        self.charged_volume_ratio = 1. - self.cap_fraction + self.full_fraction
        self.gap = max(0., pole-self.outlet)
        self.transit = transit
        self.scale = scale
        self.lane = transit.lane_factor(0., 1.)
        # The entire opening fits behind the moving round nose by this depth.
        self.open_depth = max(2.*self.gap, self.sag(self.radii[0]), 1.)
        self.open_time = transit.age_at(self.open_depth/scale) / self.lane
        # At the lip, travel equals lift. Store that offset for the airborne
        # renderer; neck volume and the time of contact still use opening().
        lo, hi = 0., self.gap
        for _ in range(32):
            travel = (lo+hi)*.5
            if travel < self._lift_at(travel):
                lo = travel
            else:
                hi = travel
        self.first_exit_lift = hi
        self.full = (self.full_fraction, None, 0.)
        self._last_time = None
        self._last_state = None

    def sag(self, x):
        return x*x / (self.radius + math.sqrt(max(0., self.radius**2-x*x)))

    def _lift_at(self, travel):
        u = min(1., travel/self.open_depth)
        return self.gap * (1.-u*u*(3.-2.*u))

    def path_to_lip(self, radial_fraction):
        """Length of a converging streamline from the initial cap to the lip."""
        path = 0.
        for lower,upper,a,b in zip(self.heights,self.heights[1:],self.radii,self.radii[1:]):
            height = upper-lower
            width_change = radial_fraction*(b-a)
            segment = math.hypot(height,width_change)
            if upper < self.pole+self.sag(radial_fraction*b):
                path += segment
                continue
            lo,hi = 0.,1.
            for _ in range(28):
                f = .5*(lo+hi)
                if lower+f*height < self.pole+self.sag(radial_fraction*a+f*width_change):
                    lo = f
                else:
                    hi = f
            return path+hi*segment
        return path

    def outlet_slope(self, diameter):
        """Wall direction over the final grain-sized contact with the throat."""
        height = min(diameter, self.heights[-1]-self.outlet)
        y = self.outlet + height
        i = max(0, min(len(self.heights)-2, bisect_right(self.heights, y)-1))
        part = (y-self.heights[i])/(self.heights[i+1]-self.heights[i])
        width = self.radii[i] + part*(self.radii[i+1]-self.radii[i])
        return (width-self.radii[0])/height

    def lane_exit_time(self, radial_fraction):
        """Earliest time the cap uncovers this radial site at the outlet."""
        offset = self.sag(radial_fraction*self.radii[0])
        lo, hi = self.first_exit_lift, self.open_depth
        for _ in range(32):
            travel = .5*(lo+hi)
            if travel-self._lift_at(travel) < offset:
                lo = travel
            else:
                hi = travel
        return self.transit.age_at(hi/self.scale) / self.lane

    def opening(self, elapsed):
        """Return the packed volume, neck outline and launch offset at this time."""
        if elapsed >= self.open_time:
            return self.full
        if elapsed <= 0.:
            return self.cap_fraction, (), self.gap
        if elapsed == self._last_time:
            return self._last_state
        travel = self.scale * self.transit.depth_at(elapsed*self.lane)
        lift = self._lift_at(travel)
        tip = self.outlet + lift - travel
        bottom = max(tip, self.outlet)
        # Intersect the advancing spherical cap with the real wall. Sampling
        # the arc by radius keeps its tip round even in the wide one-second cup.
        levels = {bottom, self.heights[-1]}
        levels.update(y for y in self.heights if y > bottom)
        for i in range(1, 17):
            y = tip + self.sag(self.radii[-1]*i/16.)
            if bottom < y < self.heights[-1]:
                levels.add(y)
        rows = []
        for y in sorted(levels, reverse=True):
            i = max(0, min(len(self.radii)-2, bisect_right(self.heights, y)-1))
            ratio = (y-self.heights[i]) / (self.heights[i+1]-self.heights[i])
            wall = self.radii[i] + ratio*(self.radii[i+1]-self.radii[i])
            h = max(0., y-tip)
            rows.append((min(wall, math.sqrt(max(0., h*(2.*self.radius-h)))), y))
        volume = sum((h-k)*(a*a+a*b+b*b)/3. for (a,h),(b,k) in zip(rows,rows[1:]))
        self._last_time = elapsed
        self._last_state = (volume/self.capacity, rows, lift)
        return self._last_state

    def upper_layer(self, remaining):
        """Drain the last packed sand across the actual width at each height."""
        volume = remaining * self.capacity
        i = max(0, min(bisect_right(self.volumes, volume)-1, len(self.radii)-2))
        a, b = self.radii[i:i+2]
        h = self.heights[i+1]-self.heights[i]
        lo, hi = 0., 1.
        for _ in range(16):
            u = .5*(lo+hi)
            r = a+(b-a)*u
            if self.volumes[i]+h*u*(a*a+a*r+r*r)/3. < volume:
                lo = u
            else:
                hi = u
        u = .5*(lo+hi)
        return [(a+(b-a)*u, self.heights[i]+h*u)] + [
            (self.radii[j], self.heights[j]) for j in range(i, -1, -1)]
