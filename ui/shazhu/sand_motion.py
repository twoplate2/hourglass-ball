"""Causal, periodic grain trajectories for offline illustrations, not a DEM."""

import numpy as np


LOOP_SECONDS = 10
CX, OUTLET, LANDING = 320.0, 226.0, 755.0
NECK_HALF_WIDTH, NECK_BOTTOM = 22.0, 231.0
FLIGHT_TIME, V0 = 1.2, 90.0
GRAVITY = 2.0 * (LANDING - OUTLET - V0 * FLIGHT_TIME) / FLIGHT_TIME**2


def periodic_delta(a, b):
    return (a - b + LOOP_SECONDS / 2.0) % LOOP_SECONDS - LOOP_SECONDS / 2.0


class SandMotion:
    def __init__(self, rate, radius, surface, cluster_gain=1.0, source_seed=30031):
        source = np.random.default_rng(source_seed)
        self.packet_time = np.sort(source.uniform(0.0, LOOP_SECONDS, 145))
        self.packet_duration = source.uniform(0.018, 0.075, len(self.packet_time))
        self.packet_x = source.uniform(-20.0, 20.0, len(self.packet_time))
        self.packet_width = source.uniform(2.0, 6.5, len(self.packet_time))
        self.packet_vx = source.uniform(-7.0, 7.0, len(self.packet_time))
        self.packet_vy = source.uniform(-5.0, 5.0, len(self.packet_time))
        self.packet_flux = source.uniform(-0.12, 0.12, len(self.packet_time))
        rng = np.random.default_rng(30030)
        count = rate * LOOP_SECONDS
        grid = np.linspace(0.0, LOOP_SECONDS, 4097)
        flux = np.ones_like(grid)
        for time, duration, amount in zip(
                self.packet_time, self.packet_duration, self.packet_flux):
            flux += amount * np.exp(-0.5 * (periodic_delta(grid, time) / duration)**2)
        flux = np.clip(flux, 0.82, 1.18)
        cdf = np.concatenate(([0.0], np.cumsum((flux[:-1] + flux[1:]) * 0.5)))
        cdf *= LOOP_SECONDS / cdf[-1]
        uniform = (np.arange(count) + rng.uniform(0.0, 1.0, count)) / rate
        self.birth = np.interp(uniform, cdf, grid)
        slot = np.searchsorted(self.packet_time, self.birth)
        before, after = (slot - 1) % len(self.packet_time), slot % len(self.packet_time)
        distance_before = np.abs(periodic_delta(self.birth, self.packet_time[before]))
        distance_after = np.abs(periodic_delta(self.birth, self.packet_time[after]))
        self.packet_id = np.where(distance_before < distance_after, before, after)
        near = np.exp(-0.5 * (
            periodic_delta(self.birth, self.packet_time[self.packet_id])
            / self.packet_duration[self.packet_id])**2)
        limit = NECK_HALF_WIDTH - radius
        self.x0 = rng.uniform(-limit, limit, count)
        self.grouped = rng.uniform(0.0, 1.0, count) < 0.36 * cluster_gain * near
        origins = self.packet_x[self.packet_id]
        widths = self.packet_width[self.packet_id]
        grouped_x = origins + rng.normal(0.0, 1.0, count) * widths
        self.x0[self.grouped] = np.clip(grouped_x[self.grouped], -limit, limit)
        self.vx = np.clip(rng.normal(0.0, 0.8, count), -2.0, 2.0)
        self.vy = np.full(count, V0)
        # The packet's influence is local in both emission time and position.
        # After emission the grain retains these properties; no frame-wise noise.
        for time, duration, x, width, vx, vy in zip(
                self.packet_time, self.packet_duration, self.packet_x,
                self.packet_width, self.packet_vx, self.packet_vy):
            temporal = np.exp(-0.5 * (periodic_delta(self.birth, time) / duration)**2)
            spatial = np.exp(-0.5 * ((self.x0 - x) / width)**2)
            weight = temporal * spatial
            self.vx += weight * vx
            self.vy += weight * vy
        self.vx = np.clip(self.vx, -8.0, 8.0)
        self.vy = np.clip(self.vy, V0 - 9.0, V0 + 9.0)
        # Surface intersection uses each grain's own trajectory, not a fixed
        # timer at the cone tip. It also supplies every landing effect's origin.
        low, high = np.zeros(count), np.full(count, 1.6)
        for _ in range(20):
            age = (low + high) * 0.5
            x, y = self.positions(age)
            airborne = y < surface(x, 0.0)
            low = np.where(airborne, age, low)
            high = np.where(airborne, high, age)
        self.hit_age = (low + high) * 0.5
        self.hit_x, self.hit_y = self.positions(self.hit_age)
        self.hit_time = (self.birth + self.hit_age) % LOOP_SECONDS
        self.hit_speed = self.vy + GRAVITY * self.hit_age
        impacts = np.random.default_rng(77030)
        self.splash_parent = np.flatnonzero(
            impacts.uniform(0.0, 1.0, count) < min(0.08, 4200.0 / count))
        self.roll_parent = np.flatnonzero(
            impacts.uniform(0.0, 1.0, count) < min(0.06, 1800.0 / count))
        parents = self.splash_parent
        self.splash_vx = (self.vx[parents] * 0.6
                          + impacts.normal(0.0, 52.0, len(parents)))
        self.splash_vy = -self.hit_speed[parents] * impacts.uniform(
            0.025, 0.10, len(parents))
        parents = self.roll_parent
        outward = np.tanh((self.hit_x[parents] - CX - 7.0) / 10.0)
        self.roll_side = np.where(
            impacts.uniform(0.0, 1.0, len(parents)) < (outward + 1.0) * 0.5, 1.0, -1.0)
        self.roll_speed = impacts.uniform(38.0, 85.0, len(parents))

    def positions(self, age, indices=None):
        x0 = self.x0 if indices is None else self.x0[indices]
        vx = self.vx if indices is None else self.vx[indices]
        vy = self.vy if indices is None else self.vy[indices]
        # Bounded relative slip preserves packet identity without amplifying a
        # tiny lateral velocity into a wind-blown whole-column oscillation.
        slip = 0.50 * (1.0 - np.exp(-age / 0.50))
        return CX + x0 + vx * slip, OUTLET + vy * age + 0.5 * GRAVITY * age**2

    def airborne(self, t):
        age = (t - self.birth) % LOOP_SECONDS
        indices = np.flatnonzero(age < self.hit_age + 0.006)
        x, y = self.positions(age[indices], indices)
        return indices, x, y
