"""Live B13 grains in reference-glass coordinates, drawn at native resolution."""
import math
import logging
import os
import sys
from functools import lru_cache
from typing import NamedTuple

import numpy as np
from grain_style import B13_REFERENCE_RADIUS as REFERENCE_RADIUS, B13_TEXTURE_SPEED, hourglass_transit


# The B13 reference frame has a 417.69 px inner diameter and a 15% opening.
REFERENCE_HALF = .15 * REFERENCE_RADIUS
RATE = 14500
BLOCK = 5 * RATE
HEAD_ID_START = -(1 << 46)  # Disjoint from all ordinary material phase indices.


class Grains(NamedTuple):
    serial: np.ndarray
    x: np.ndarray
    depth: np.ndarray
    radius: np.ndarray
    tone: np.ndarray


class ReceivingLayer(NamedTuple):
    boundary: float
    residence: float



@lru_cache(maxsize=1)
def _birth_material():
    # Same sample order as the B11/B13 study. These RNGs never touch game state.
    rng = np.random.default_rng(319)
    offsets = rng.random(BLOCK)
    lanes = rng.triangular(-1., 0., 1., BLOCK)
    broad = rng.random(BLOCK) < .37
    lanes[broad] = 2. * rng.beta(1.65, 1.65, broad.sum()) - 1.
    jitter = rng.uniform(-1., 1., BLOCK)
    tone = rng.uniform(-1., 1., BLOCK)
    radius = rng.uniform(.51, .94, BLOCK)
    keys = np.arange(0., 5.4, .075)
    signals = np.random.default_rng(808).uniform(-1., 1., (2, len(keys)))
    birth = (np.arange(BLOCK) + offsets) / RATE
    drift = np.where(lanes < 0., np.interp(birth, keys, signals[0]),
                     np.interp(birth, keys, signals[1]))
    # The same wall slip as the packed neck. Each grain keeps its lane variation.
    slip = 1. - .09 * lanes**2 - .012 * np.abs(jitter)
    inward = .08 * np.sin(np.pi * lanes)
    # Some moving outer grains continue along the lip before drifting inward.
    # The centre and outermost lane keep their original positions.
    # A packed cross-section carries grains up to both walls. Map the
    # centre-biased free-stream lanes to equal shares of the mouth, then fade
    # that offset downstream on the very same persistent grains.
    order = np.argsort(lanes)
    packed_lanes = np.empty_like(lanes)
    packed_lanes[order] = (np.arange(BLOCK) + .5) * (2. / BLOCK) - 1.
    lip_shift = packed_lanes - lanes
    # Neighbouring grains can start together, with individual grains leading
    # their neighbours. Keep those offsets attached to the material as it falls.
    release = np.roll(offsets, -541)
    patch = np.interp(packed_lanes, np.linspace(-1., 1., 9), offsets[:9])
    first_lag = (.70*release + .30*patch*np.minimum(1., 6.*release)) * (1. - .55*np.abs(packed_lanes))
    outer = np.clip((np.abs(lanes) - .48) / .46, 0., 1.) ** 1.3
    radius += .13 * np.maximum(0., 1. - (lanes / .68)**2)
    return offsets, lanes, jitter, tone, radius, drift, slip, inward, outer, lip_shift, first_lag


def _pigment_depth(age, duration):
    """Distance of visible grains in reference pixels, measured from the lip."""
    base = B13_TEXTURE_SPEED
    # The first ~12 px use the same wall-matched motion as the packed neck.
    lip_time, lip_acceleration = .15, 9.25
    lip_age = np.minimum(age, lip_time)
    lip_distance = lip_age * (base + .5 * lip_acceleration * lip_age)
    exit_speed = base + lip_acceleration * lip_time

    # Keep the one-second setting gentle. Longer settings share the same
    # visible gravity and limiting speed regardless of their aperture.
    u = min(1., max(0., (duration - 1.) / 4.))
    blend = u*u*(3. - 2.*u)
    acceleration = lip_acceleration + (36. - lip_acceleration) * blend
    terminal = base * (1.36 + .79 * blend)
    ramp = 2.4 - lip_time
    knee_speed = exit_speed + acceleration * ramp
    relax = (terminal - knee_speed) / acceleration
    free_age = age - lip_age
    early = np.minimum(free_age, ramp)
    later = free_age - early
    return (lip_distance + exit_speed * early + .5 * acceleration * early**2 + terminal * later
            + (terminal-knee_speed) * relax * np.expm1(-later/relax))


@lru_cache(maxsize=8)
def _opening_pattern(seed):
    rng = np.random.default_rng(seed)
    phase_a, phase_b = rng.uniform(0., 2.*np.pi, 2)
    return int(rng.integers(BLOCK)), phase_a, phase_b


def _jet_slenderness(half):
    return min(1., max(0., (REFERENCE_RADIUS/half - 4.)/2.))


def _opening_slip(across, scatter, phase, phase_a, phase_b):
    return (.16*np.sin(7.4*across + 1.30*scatter + phase + phase_a)
            + .10*np.sin(12.3*across - 1.10*scatter - .83*phase + phase_b)
            + .12*across*np.sin(.58*phase + phase_b))


def _flow_grains(time, end, half, gap, grain_scale=1., max_depth=None, stride=1, inlet_lift=0.,
                  *, opening_seed=0):
    """Carry the first material with the nose; advect packed pigment behind it."""
    (offsets, lanes, jitter, tones, radii, edge_drift, slip,
     inward, outer, lip_shift, first_lag) = _birth_material()
    width_ratio = half / REFERENCE_HALF
    settling = max(0., time - end)
    time = min(time, end)
    transit = hourglass_transit(end, half, time)
    # At a shared speed a wider mouth emits more visible grains per second.
    rate = 100. * B13_TEXTURE_SPEED * width_ratio / grain_scale**2
    limit = max(0., gap if max_depth is None else max_depth) + inlet_lift + 2. * grain_scale
    carrier_tip = transit.depth_at(time * transit.lane_factor(0., 1.))
    limit = min(limit, carrier_tip + grain_scale)
    lifetime = (limit + 3.) / (B13_TEXTURE_SPEED * (1. - .09 - .012))
    # These are material coordinates of the established texture. Each also
    # has a moving carrier constraint, so it cannot enter ahead of the first batch.
    first = math.floor((time - lifetime) * rate)
    last = math.ceil(time * rate)
    first += (-first) % stride
    serial = np.arange(first, max(first, last), stride, dtype=np.int64)
    block, slot = np.divmod(serial, BLOCK)
    ids = (slot + block * 24317) % BLOCK
    birth = (serial + offsets[slot]) / rate
    lane = lanes[ids]
    lane_speed = transit.lane_factor(lane, jitter[ids])
    age = np.maximum(0., time - birth)
    depth = _pigment_depth(age * slip[ids], end)
    # A short packet crosses the lip in longitudinal order. A lag is a
    # distance behind the first grain on its lane, not a new random draw per frame.
    head_length = 16. + .85 * half
    leading_depth = transit.depth_at(time * transit.lane_factor(1., -1.))
    # Separate the first grains gradually after they leave the packed lip.
    # The first packet and the following pigment share the same handover.
    opened = min(1., max(0., (leading_depth-12.)/(2.5*head_length+half)))
    opened *= opened * (3. - 2.*opened)
    # Local slip below already opens gaps at the nose. Keep the rest of the
    # packet closer together so the falling column carries its visible volume.
    # Small release-time differences spread the first packet along a long
    # free fall. Carry its rear boundary along with it when joining the jet.
    free_run = max(0., leading_depth - 4.*half)
    # A jet only a few grains across already has separated leaders.
    spread_room = min(1., max(0., (half/grain_scale - 6.)/10.))
    extra_span = spread_room * min(1.6*half, .24*free_run)
    # Only a resolved, slender jet needs the full longitudinal release spacing.
    packet_spread = spread_room * _jet_slenderness(half)
    stretch = 1. + .08*opened + extra_span/head_length
    stagger = .80*half*opened + .55*extra_span
    moving_length = stretch*head_length
    width = .23*moving_length
    pigment_count = len(serial)
    # The spread of release positions is finite, even on a long free fall.
    release_depth = 3.2*half*packet_spread
    if leading_depth-release_depth <= limit + 4.*head_length + 3.*half:
        # Match each patch of interior pigment onto its advancing carrier
        # continuously where the packed first batch opens into the free jet.
        full_flight = hourglass_transit(end, half)
        floor_depth = 2.*REFERENCE_RADIUS
        empty_flight = full_flight.age_at(floor_depth)
        release_span = min(head_length, 1.2*half) * min(1., empty_flight/.55)
        join = moving_length * (.48 + .52 * offsets[ids]) + stagger * first_lag[ids]
        join += .38*release_span*opened
        edge = transit.depth_at(time * lane_speed)
        # The following stream meets the trailing end of the whole packet.
        if release_depth:
            free_fraction = -np.expm1(-np.maximum(0., edge-2.*half)/(2.*half))
            join += .50*release_depth * free_fraction**2
        remaining = edge - join - depth
        blend = np.clip(.5 + .5 * remaining / width, 0., 1.)
        depth -= width * (1. - blend)**2 + np.maximum(-width - remaining, 0.)
        visible = offsets[(ids+127) % BLOCK] < blend

        density = rate / B13_TEXTURE_SPEED
        packet_size = math.ceil(head_length*density)
        front_ids = np.arange((-HEAD_ID_START) % stride, packet_size, stride, dtype=np.int64)
        first_slot, phase_a, phase_b = _opening_pattern(opening_seed)
        # Change the release order of the same material. A new round has the
        # same number, sizes and lane distribution of available grains.
        keys = ((front_ids + first_slot) % max(1, packet_size) + 14717) % BLOCK
        lag = (front_ids + offsets[keys]) / density
        # Nearby leading grains share some slip but can overtake each other.
        # Travel sets their phase continuously, including on dropped frames.
        # Keep the slower shape change on very short timers. On longer
        # flights adjacent groups exchange the lead over a shorter distance.
        ramp = min(1., max(0., (end - 1.) / 4.))
        phase_length = 12. + .34*half - ramp*(2. + .14*half)
        phase = max(0., leading_depth - 12.) / phase_length
        across = lanes[keys] + lip_shift[keys]
        # Adjacent lanes can be in different phases of slip when the grains
        # separate. The phase stays attached to each grain across frames.
        scatter = jitter[keys] * (1. + (np.pi-1.)*packet_spread)
        shift = _opening_slip(across, scatter, phase, phase_a, phase_b)
        # Give the first layers room to separate in free flight. Material
        # spacing is independent of lane speed; nearby grains share some slip.
        spacing = offsets[(keys+919) % BLOCK]
        order = np.clip(lag/head_length, 0., 1.)
        front_weight = (1.-order)**2
        radial = abs(across)
        lane_spacing = (1.40 - 1.32*radial)*spacing + .11*radial**2
        packed_lag = first_lag[keys]*(1. - .30*(1.-order)*radial) + front_weight*lane_spacing
        # Once resolved, release spacing follows individual grains across the
        # whole aperture. A shared wall-dependent lag bunches the shoulders.
        free_lag = offsets[keys] + front_weight*spacing
        release_lag = packed_lag + packet_spread*(free_lag-packed_lag)
        # In a resolved jet the departure delay is set at release. Reusing
        # the current nose phase here could pull an airborne grain back up.
        exit_shift = shift
        if packet_spread:
            at_lip = _opening_slip(across, scatter, 0., phase_a, phase_b)
            exit_shift = shift + packet_spread*(at_lip-shift)
        lane_slip = exit_shift*np.where(exit_shift > 0., 1., np.minimum(1.1, 1.8*release_lag))
        lag_distance = stagger*(release_lag + front_weight*lane_slip)
        interval = .60*min(head_length, 1.2*half)*(np.sqrt(order)-order)
        separation = (1. - .80*radial)*opened*interval
        front_lane = lanes[keys]
        front_speed = transit.lane_factor(front_lane, jitter[keys])
        # The earliest grains already have open space ahead of them. Let their
        # neighbours fall progressively behind, without delaying first contact.
        neighbours = np.minimum(1., lag/(grain_scale*(1.5 - .4*spacing)))
        lane_tip = transit.depth_at(time*front_speed)
        front = lane_tip - stretch*lag - neighbours*(separation + lag_distance)
        # Each grain keeps its place in the initial release. After it clears
        # the lip the leading neighbours pull apart before those behind them.
        material_lag = lag + neighbours*(.80*half*release_lag + interval)
        face_weight = np.maximum(0., 1.-(material_lag/(3.8*half))**2)**3
        spread = .35 + .48*radial + .20*offsets[(keys+143) % BLOCK]
        extra_lag = material_lag*face_weight*spread + .82*release_span*order
        # A monotone function of this grain's own travel keeps it falling
        # forward even when the leading surface is much faster than the texture.
        free = np.maximum(front-12., 0.)
        opened_grain = free/(free + .30*half + .35*extra_lag)
        front -= extra_lag*opened_grain**2
        # Open the release layer smoothly. Once open it travels with the jet.
        free_travel = np.maximum(0., front-2.*half)
        free_fraction = -np.expm1(-free_travel/(2.*half))
        # Leading grains share more of their lane's motion. The rear of the
        # packet takes up the release spacing and meets the following stream.
        behind = lane_tip - front
        packet_length = moving_length + stagger
        carried = packet_length / (packet_length + behind)
        carry_weight = front_weight + spread_room*(carried-front_weight)
        front += behind*(.36 + .48*packet_spread)*carry_weight*free_fraction
        # Apply the local slip after the common forward motion. This includes
        # the leaders with almost zero release lag, so neighbours can pass them.
        nose = np.maximum(0., 1. - 2.*order)**2
        front -= half*packet_spread*nose*free_fraction*np.maximum(0., .12 + shift)
        # Include the grain's own release spacing in its relaxation length.
        # The separation slope starts at zero and stays below 2/e.
        release_lag_distance = release_depth * np.sqrt(order) * (.60+.40*spacing)
        travel = np.maximum(front, 0.) / (.5*(half + release_lag_distance))
        front -= release_lag_distance * (1. - (1. + travel)*np.exp(-travel))
        # Two neighbouring departures reach a larger spacing as the stream gains
        # speed. Measure it against the existing contact clock for this aperture.
        release_time = empty_flight - full_flight.age_at(floor_depth-half)
        travel_now = full_flight.depth_at(time)
        previous = full_flight.depth_at(max(0., time-release_time))
        relative_spacing = min(1., (travel_now - previous)/half)
        # Broad, packed outlets start with one shared face. Where the outlet
        # resolves separate lanes, retain their existing departure offsets.
        packed_gap = np.maximum(0., carrier_tip - front - lag)
        lane_gap = np.maximum(0., lane_tip - front - lag)
        packed_gap *= 1. - .80*relative_spacing
        lane_gap *= .75*(1. - relative_spacing)
        # A lateral difference smaller than one drawn grain still shares a face.
        lane_share = max(0., packet_spread*half - grain_scale) / max(half - grain_scale, grain_scale)
        front += front_weight**2 * (packed_gap + lane_share*(lane_gap - packed_gap))
        fraction = np.clip((head_length - lag) / (.52*head_length), 0., 1.)
        active = ((front >= 0.) & (front <= limit) & (offsets[(keys+193) % BLOCK] < fraction))
        pigment = serial[visible]
        pigment_count = len(pigment)
        serial = np.concatenate((pigment, HEAD_ID_START + front_ids[active]))
        ids = np.concatenate((ids[visible], keys[active]))
        depth = np.concatenate((depth[visible], front[active]))
        # Head grains keep their outlet lanes as differences in depth open the nose.
        lane = np.concatenate((lane[visible], front_lane[active]))
        lane_speed = np.concatenate((lane_speed[visible], front_speed[active]))
    flight_age = transit.age_at(depth)
    lip_time = time - flight_age / lane_speed
    released = lip_time >= -1.e-9
    # The first packet already crossed its forward trajectory's lip test.
    # Inverting an interpolated clock here can hide its very first grain for a frame.
    released[pigment_count:] = True
    keep = (depth > inlet_lift) & released & (lip_time < end) & (depth <= limit)
    serial, ids, lane, depth = (v[keep] for v in (serial, ids, lane, depth))
    if settling > 0. and depth.size:
        # Continue the exact grains that were in flight at cutoff. Their depth
        # at that instant fixes their place on the existing bulk trajectory.
        advance = transit.depth_at(flight_age[keep] + settling*lane_speed[keep]) - depth
        # The receding surface crosses the lip at the packed volume rate Q/A.
        # Ease onto free travel over the first fraction of a mouth radius.
        packed_travel = settling * (4.*REFERENCE_RADIUS**3 / (3.*end*half**2))
        advance -= np.maximum(0., advance-packed_travel) * np.exp(-advance / (.38*half))
        distance = advance / (.34*half + 2.*grain_scale)
        spread = -.50 * half * np.expm1(-distance**2)
        # The shoulders of the rear surface separate ahead of its centre.
        # Use each grain's fixed lane at the packed lip, also on coarse sand.
        radial = np.abs(lane + lip_shift[ids])
        scatter = .47*jitter[ids] + .35*(2.*offsets[ids]-1.) + .90*(radial*(2.-radial) - .10)
        # Only the last packed layer has this extra lag. Let its slow grains
        # catch the established flow as they travel beyond the lip.
        lag_limit = .50 * np.exp(-(.60 * distance)**4)
        depth += advance + spread * np.maximum(scatter, -lag_limit)
        visible = depth <= limit
        serial, ids, lane, depth = (v[visible] for v in (serial, ids, lane, depth))
    depth -= inlet_lift
    x = _stream_x(depth, lane, ids, half, grain_scale, gap, carrier_tip, limit, inlet_lift)
    size = radii[ids] * grain_scale
    return Grains(serial, x, depth, size, tones[ids])


def _stream_x(depth, lane, ids, half, grain_scale, gap, carrier_tip, limit, inlet_lift):
    _, _, jitter, _, _, edge_drift, _, inward, outer, lip_shift, _ = _birth_material()
    width_ratio = half / REFERENCE_HALF
    released = np.maximum(depth, 0.)
    narrow = 1. - .13 * (1. - np.exp(-released / 54.))
    settle = np.clip((released - 24.) / 145., 0., 1.)
    lateral = lane - inward[ids] * settle
    near_lip = np.clip(1. - released / max(12., 3.0 * half), 0., 1.)
    packed = near_lip**2 * (3. - 2.*near_lip)
    # Only the grains following the free face still share its wider lanes.
    # Fade this local shoulder as the front moves away from each grain.
    if carrier_tip - limit < 5.*half:
        distance = (carrier_tip-depth-inlet_lift)/half
        shoulder = np.clip(distance / 2., 0., 1.)
        shoulder *= shoulder*(3.-2.*shoulder)
        fade = np.clip((5.-distance)/3., 0., 1.)
        packed += .48*(1.-packed)*shoulder*fade**2*(3.-2.*fade)
    lateral += lip_shift[ids] * packed
    x = lateral * max(0., half - .40 * grain_scale) * narrow
    travel = 1. - np.exp(-np.maximum(0., depth - 12.) / 53.)
    contact = np.clip((gap - depth) / 21., 0., 1.)
    # Let individual edge grains fan out without a whole band swaying together.
    x += (edge_drift[ids] * width_ratio * 1.6 + jitter[ids] * 2.6) * outer[ids] * travel * contact
    return x


@lru_cache(maxsize=32)
def _first_contact(end, half, floor):
    transit = hourglass_transit(end, half)
    return transit.age_at(floor) / transit.lane_factor(0.0, 1.0)


def _material_keys(serial, half, scale, seed):
    block, slot = np.divmod(serial, BLOCK)
    keys = (slot + block * 24317) % BLOCK
    head = serial < HEAD_ID_START // 2
    density = 100.0 * half / REFERENCE_HALF / scale**2
    count = math.ceil((16.0 + 0.85 * half) * density)
    start = _opening_pattern(seed)[0]
    keys[head] = ((serial[head] - HEAD_ID_START + start) % count + 14717) % BLOCK
    return keys


def _inverse_flight(depth, flow, lift, entry, gravity):
    if hasattr(flow, "seconds_per_pixel"):
        return flow.age_at(lift + depth) - flow.age_at(lift)
    return 2.0 * depth / (entry + np.sqrt(entry**2 + 2.0 * gravity * depth))


@lru_cache(maxsize=8)
def _section_face(half, scale, seed):
    """Height of the exposed surface of a gravity-settled section at the lip."""
    from opening_packing import settled_section, support_paths

    radii = _birth_material()[4] * scale
    rng = np.random.default_rng(seed)
    count = math.ceil(6.0 * half**2 / np.mean(radii**2))
    packed = settled_section(half, half, radii, rng.integers(0, len(radii), count), rng.random(count))
    centres, heights, sizes, _ = np.transpose(packed)
    lanes = np.linspace(-half, half, max(9, math.ceil(2.0 * half / scale)))
    support = np.array(support_paths(packed))[:,0]-heights
    surface = np.full_like(lanes, np.nan)
    clearance = np.full_like(lanes, np.nan)
    basal = np.full_like(lanes, np.nan)
    for i, lane in enumerate(lanes):
        distance = np.maximum(0., np.abs(centres-lane)-scale/2.)
        inside = np.flatnonzero(distance <= sizes)
        if inside.size:
            edge = heights[inside] + np.sqrt(sizes[inside]**2-distance[inside]**2)
            leading = edge.argmax()
            surface[i] = edge[leading]
            clearance[i] = support[inside[leading]]
            basal[i] = np.min(heights[inside] - np.sqrt(sizes[inside]**2-distance[inside]**2))
    supported = np.isfinite(surface)
    surface = np.interp(lanes, lanes[supported], surface[supported])
    clearance = np.interp(lanes, lanes[supported], clearance[supported])
    basal = np.interp(lanes, lanes[supported], basal[supported])
    return lanes/half, surface, clearance, basal, np.asarray(packed)


def _free_surface(half, scale, seed):
    lanes, surface, _, _, _ = _section_face(half, scale, seed)
    return lanes, surface.max()-surface


def _lane_at(table, level, lane):
    """Interpolate a material section and transverse coordinate on uniform grids."""
    level = np.clip(level, 0.0, len(table) - 1)
    lateral = np.clip((lane + 1.0) * (table.shape[1] - 1) / 2.0, 0.0, table.shape[1] - 1)
    row = np.minimum(level.astype(int), len(table) - 2)
    col = np.minimum(lateral.astype(int), table.shape[1] - 2)
    left = table[row, col] + (lateral - col) * (table[row, col + 1] - table[row, col])
    right = table[row + 1, col] + (lateral - col) * (table[row + 1, col + 1] - table[row + 1, col])
    return left + (level - row) * (right - left)


def _contact_lanes(lanes, material, half):
    # Neighbours in the packed cross-section share its area. Keep each grain's
    # transverse rank while relaxing local concentrations across the mouth.
    radius = np.mean(material.radius)
    heights = np.linspace(0.0, material.depth.max(), max(3, round(4.0 * material.depth.max() / half)))
    cols = max(7, round(half / (2.0 * radius)))
    nearest = np.clip(np.rint((lanes + 1.0) * (cols - 1) / 2.0).astype(int), 0, cols - 1)
    area = material.radius**2
    one_layer = radius * half / cols
    packed = np.empty((len(heights), cols))
    for i, height in enumerate(heights):
        proximity = np.maximum(0.0, 1.0 - abs(material.depth - height) / (half / 2.0))
        weights = one_layer + np.bincount(nearest, area * proximity, minlength=cols)
        cumulative = np.cumsum(weights) - weights / 2.0
        packed[i] = 2.0 * (cumulative - cumulative[0]) / (cumulative[-1] - cumulative[0]) - 1.0
    return _lane_at(packed, material.depth / heights[-1] * (len(heights) - 1), lanes)


def _packed_lanes(lanes, travel, half, scale, seed, flow, material, confinement):
    positions, cover = _free_surface(half, scale, seed)
    # Slip through the confined section delays material near a wall.
    cover = cover + confinement * half * (1.0 / flow.lane_factor(positions, 1.0) - 1.0)
    step = scale / 2.0
    levels = np.arange(0.0, cover.max() + 2.0 * scale, step)
    ranks = np.linspace(0.0, 1.0, len(positions))
    available = np.empty((len(levels), len(positions)))
    fraction = np.empty(len(levels))
    for i, level in enumerate(levels):
        # Integrate the width uncovered by the measured surface. At full
        # exposure the inverse is exactly the original transverse coordinate.
        widths = np.clip((level + scale - cover) / scale, 0.0, 1.0)
        cumulative = np.r_[0.0, np.cumsum((widths[1:] + widths[:-1]) / 2.0)]
        fraction[i] = cumulative[-1] / (len(positions) - 1)
        available[i] = np.interp(ranks * cumulative[-1], cumulative, positions)
    if confinement:
        # Invert the occupied area of the settled face to locate the layer
        # exposed by each departure. Deeper than the face the full lip flows.
        area = np.r_[0.0, np.cumsum(0.5 * step * (fraction[1:] + fraction[:-1]))]
        order = np.argsort(-material.depth)
        disks = material.radius[order] ** 2
        density = 100.0 * half / REFERENCE_HALF * np.mean(_birth_material()[4] ** 2)
        charge = np.empty_like(travel)
        charge[order] = (np.cumsum(disks) - 0.5 * disks) / density
        packed = np.interp(charge, area, levels) + np.maximum(0.0, charge - area[-1])
        # The wall changes which part of the free face is exposed. The
        # occupied disk area is conserved across every resolved aperture.
        travel = packed
    support = -np.expm1(-travel / (2.0 * np.mean(material.radius)))
    return _lane_at(available, travel / step, lanes) - lanes, travel, support


def _relax_dense_feed(material, maps, times, grid, head, half, entry, gravity):
    """Redistribute projected area through the dense inlet at constant discharge."""
    gravity = max(0.0, gravity)
    contact = times[-1]
    window = contact - times[0]
    # In the dense part, constant through-flow fixes occupied area per
    # unit travel time. Use the same pigment, so its cumulative projected
    # grain area is exactly conserved when contact pressure relaxes.
    order = np.argsort(material.depth)
    mass = np.interp(grid, material.depth[order], np.cumsum(material.radius[order] ** 2))
    for t, positions in zip(times, maps):
        nose = np.interp(head, grid, positions)
        boundary = nose - 2.5 * half
        if boundary <= 0.0:
            continue
        at_lip, at_face = np.interp([0.0, boundary], positions, mass)
        if at_face <= at_lip:
            continue
        section = (positions > 0.0) & (positions < boundary)
        fraction = (mass[section] - at_lip) / (at_face - at_lip)
        span = 2.0 * boundary / (entry + np.sqrt(entry**2 + 2.0 * gravity * boundary))
        age = np.clip(fraction, 0.0, 1.0) * span
        relaxed = age * (entry + 0.5 * gravity * age)
        remaining = (contact - t) / window
        weight = remaining**2 * (3.0 - 2.0 * remaining)
        positions[section] += weight * (relaxed - positions[section])


class AirborneFlightGrid(NamedTuple):
    times: np.ndarray
    material_depth: np.ndarray
    positions: np.ndarray


class OpeningFlight(NamedTuple):
    material: Grains
    keys: np.ndarray
    birth: np.ndarray
    contact: float
    entry: float
    gravity: float
    times: np.ndarray
    maps: np.ndarray
    grid: np.ndarray
    settled_clock: np.ndarray
    crossings: np.ndarray
    lip_shift: np.ndarray
    pressure: np.ndarray
    advance: np.ndarray
    unloading_time: float
    packed_lag: np.ndarray
    area_shift: np.ndarray
    receiving: ReceivingLayer | None
    airborne: AirborneFlightGrid | None = None


def _unloaded_fraction(time, end, duration):
    u = np.clip(1.0 + (time - end) / duration, 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


@lru_cache(maxsize=4)
def _opening_material(end, half, floor, lift, scale, seed, neck):
    flow = hourglass_transit(end, half)
    exit_time = flow.age_at(lift) / flow.lane_factor(0.0, 1.0)

    # Locate the first material that reaches the curved empty glass. Use
    # unoccluded positions up to the surface, just as the renderer does.
    def material_at(t):
        neck_lift = neck.opening(t)[2] / neck.scale if neck else 0.0
        return _flow_grains(t, end, half, floor, scale, floor + half, 1, neck_lift, opening_seed=seed)

    contact = _first_contact(end, half, floor)
    lo, hi = max(exit_time, contact * 0.85), contact * 1.20
    for _ in range(18):
        t = (lo + hi) * 0.5
        grains = material_at(t)
        radial = np.minimum(abs(grains.x), REFERENCE_RADIUS)
        sag = radial**2 / (REFERENCE_RADIUS + np.sqrt(REFERENCE_RADIUS**2 - radial**2))
        if (grains.depth + sag + grains.radius >= floor).any():
            hi = t
        else:
            lo = t
    contact = hi
    material = material_at(contact)
    _, lip_velocity = flow.motion(exit_time, 0.0, 1.0, 0.0, 1.0)
    entry = max(B13_TEXTURE_SPEED, -lip_velocity)
    ballistic = not hasattr(flow, "seconds_per_pixel")
    if ballistic:
        # A filled mouth already discharges under the weight of the loose
        # layer above it. Use the opening radius and gravity scale of the
        # glass with the packed discharge coefficient. An initially
        # empty neck can already have a higher measured speed at first exit.
        usable_half = max(scale, half - float(np.mean(material.radius)))
        entry = max(entry, 0.80 * np.sqrt(2.0 * floor * half) / contact)
    duration = contact - exit_time
    gravity = 2.0 * (floor / duration - entry) / duration
    # The dense column and the unsupported nose have different velocities at
    # first impact. Measure transport from the same material, excluding grains
    # entering or leaving the view during this derivative measurement.
    dt = min(0.001, duration / 100.0)
    a, b = material_at(contact - dt), material_at(contact + dt)
    unique, ia, ib = np.intersect1d(a.serial, b.serial, return_indices=True)
    _, im, iu = np.intersect1d(material.serial, unique, return_indices=True)
    depths = material.depth[im]
    velocities = (b.depth[ib[iu]] - a.depth[ia[iu]]) / (2.0 * dt)
    order = np.argsort(depths)
    depths, velocities = depths[order], velocities[order]
    grid = np.linspace(0.0, max(float(material.depth.max()), floor) + 2.0 * scale, 257)
    radius = max(2.0 * scale, (grid[-1] - grid[0]) / 128.0)
    lower, upper = (np.searchsorted(depths, grid + offset) for offset in (-radius, radius))
    velocity = np.array([np.median(velocities[l:r]) if l < r else np.nan for l, r in zip(lower, upper)])
    valid = np.isfinite(velocity) & (velocity > 0.0)
    velocity = np.interp(grid, grid[valid], velocity[valid])
    # Backtrace a single positive axial velocity field for the final neck
    # residence. Each characteristic map is increasing, so no parcel passes
    # an earlier one. At contact the map is the identity on the original grains.
    residence = neck.full_fraction * end if neck else half / entry
    window = min(residence, 2.0 * half / entry, 0.5 * duration)
    if ballistic:
        window = min(2.0 * residence, 4.0 * half / entry, 0.60 * duration)
    times = np.linspace(contact - window, contact, max(2, int(math.ceil(window * 240.0)) + 1))
    maps = np.empty((len(times), len(grid)))
    maps[-1] = grid
    if hasattr(flow, "seconds_per_pixel"):
        free = 1.0 / (flow.seconds_per_pixel * flow.relative_area(lift + grid))
    else:
        free = np.sqrt(entry * entry + 2.0 * gravity * grid)
    head = float(material.depth.max())
    head_velocity = np.interp(head, grid, velocity)
    if ballistic:
        slenderness = _jet_slenderness(half)
        unsupported = (1.0 / 3.0 + slenderness / 15.0) * usable_half
        strain_rate = 2.5 * np.sqrt(max(gravity, 0.0) / half)
        slip_limit = (0.25 + slenderness / 12.0) * np.sqrt(2.0 * max(gravity, 0.0) * half)
    for i in range(len(times) - 2, -1, -1):
        step = times[i + 1] - times[i]
        u = (0.5 * (times[i] + times[i + 1]) - (contact - window)) / window
        w = u * u * (3.0 - 2.0 * u)
        speed = (1.0 - w) * free + w * velocity
        if ballistic:
            # Nearby grains share their acceleration while still in contact.
            # Across the free face the unsupported nose pulls clear of the
            # dense interior. Limit the velocity deficit to its discharge speed.
            nose = np.interp(head, grid, maps[i + 1])
            tip_speed = (1.0 - w) * np.interp(nose, grid, free) + w * head_velocity
            lag = np.maximum(0.0, nose - grid - unsupported)
            margin = max(1.0e-9, min(max(1.0, tip_speed - entry), slip_limit))
            rarefaction = tip_speed - margin * np.tanh(strain_rate * lag / margin)
            # The full mouth fixes the incoming speed. Accelerate downstream
            # over the residence length of material leaving the confined neck.
            discharge = (1.0 - u) * entry + u * velocity[0]
            rarefaction = discharge + (rarefaction - discharge) * (-np.expm1(-grid / (1.2 * half)))
            # Dense material upstream completes its transfer before the
            # unsupported nose. Evolve velocities before integrating any
            # trajectory, keeping every section of the field positive.
            bulk = -np.expm1(-((lag / (3.0 * half)) ** 2))
            coast = 0.06 * bulk
            ramp = 0.125 + 0.02 * bulk
            settled = 1.0 - coast
            end_blend = np.clip((settled - u) / ramp, 0.0, 1.0)
            # The time-integrated transport must not shrink when the
            # established region is reached earlier. For quadratic ramps
            # the mean deficit is 2/3 of each ramp duration.
            rise = 0.5 - 1.5 * coast - (ramp - 0.125)
            blend = np.minimum(1.0, u / rise) ** 2 * end_blend**2
            speed += blend * (rarefaction - speed)
        # Integrating slowness gives the positive time of travel between
        # depths; interpolation of its inverse preserves material order.
        travel = np.r_[0.0, np.cumsum(0.5 * np.diff(grid) * (1.0 / speed[:-1] + 1.0 / speed[1:]))]
        age = np.interp(maps[i + 1], grid, travel) + np.minimum(maps[i + 1], 0.0) / speed[0] - step
        maps[i] = np.interp(age, travel, grid) + np.minimum(age, 0.0) * speed[0]
    if ballistic:
        _relax_dense_feed(material, maps, times, grid, head, half, entry, gravity)
    start_depth = float(np.interp(head, grid, maps[0]))
    flight = times[0] - exit_time
    if hasattr(flow, "seconds_per_pixel"):
        # In a capacity-limited glass this coordinate is volume divided by Q.
        # At the start of the transfer the head must carry exactly the volume
        # that has crossed the lip since the empty throat opened.
        ftime = flow.age_at(lift)
        measured = flow.age_at(lift + start_depth) - ftime
        for i, ts in enumerate(times):
            fraction = (contact - ts) / window
            blend = fraction**2 * (3.0 - 2.0 * fraction)
            factor = 1.0 + blend * (flight / measured - 1.0)
            above = np.maximum(0.0, maps[i])
            age = flow.age_at(lift + above) - ftime
            maps[i] = flow.depth_at(ftime + factor * age) - lift + np.minimum(maps[i], 0.0) * factor
    elif flight > 0.0:
        gravity = 2.0 * (start_depth / flight - entry) / flight
    start = maps[0]
    flight_age = _inverse_flight(np.maximum(start, 0.0), flow, lift, entry, gravity)
    birth = times[0] - np.interp(material.depth, grid, flight_age + np.minimum(start, 0.0) / entry)
    keys = _material_keys(material.serial, half, scale, seed)
    settled_clock = np.r_[
        0.0, np.cumsum(0.5 * np.diff(grid) * (1.0 / velocity[:-1] + 1.0 / velocity[1:]))
    ]
    # Crossing times inside the short transfer interval follow from the
    # positive axial transport. Invert its lip position for the remaining IDs.
    leading_at_lip = np.array([np.interp(0.0, positions, grid) for positions in maps])
    crossings = np.where(
        birth < times[0], birth, np.interp(material.depth, leading_at_lip[::-1], times[::-1])
    )
    pigment = _birth_material()
    lanes = (pigment[1] + pigment[9])[keys]
    lip_shift = np.zeros_like(lanes)
    pressure = np.zeros_like(lanes)
    advance = np.zeros_like(birth)
    unloading_time = half / entry
    if half > 2.0 * scale:
        pressure = half * (_contact_lanes(lanes, material, half) - lanes)
        travel = entry * (crossings - crossings.min())
        confinement = _jet_slenderness(half) if ballistic else 0.0
        shift, packed, support = _packed_lanes(lanes, travel, half, scale, seed, flow, material, confinement)
        lip_shift = half * shift
        if confinement:
            # Interpolate ordered clocks according to the contact-supported
            # fraction of this layer. Following the unloading wave, free
            # grains recover the flight times of the established stream.
            order = np.argsort(-material.depth)
            free_clock = birth[order]
            packed_clock = np.minimum(free_clock, exit_time + packed[order] / entry)
            desired = free_clock - (2.0 / 3.0) * support[order] * (free_clock - packed_clock)
            # A layer can leave only after its upstream supply is available
            # and its predecessor has cleared the lip. The smaller of the two
            # discharge intervals is the fastest spacing either state supports.
            interval = np.minimum(np.diff(free_clock), np.diff(packed_clock))
            earliest = np.r_[0.0, np.cumsum(interval)]
            supported_clock = earliest + np.maximum.accumulate(desired - earliest)
            advance[order] = np.maximum(0.0, free_clock - supported_clock)
            birth = birth - advance
            # The front travels one aperture radius while the last packed
            # contacts unload. Bound the clock rate so every grain advances.
            final_speed = math.sqrt(entry**2 + 2.0 * max(gravity, 0.0) * half)
            unloading_time = max(2.0 * half / (entry + final_speed), 2.0 * advance.max())
            lo, hi = birth.copy(), birth + advance
            for _ in range(24):
                mid = (lo + hi) * 0.5
                active = mid - birth >= advance * _unloaded_fraction(mid, times[0], unloading_time)
                lo, hi = np.where(active, lo, mid), np.where(active, mid, hi)
            crossings = np.where(birth + advance < times[0], hi, crossings)
    # Material distance behind the free surface in the filled neck. Accumulate
    # projected disk area in flight order; this does not give a grain a new
    # vertical offset or a new identity when a neighbour leaves.
    order = np.argsort(-material.depth)
    disk = material.radius[order] ** 2
    packed_lag = np.empty_like(birth)
    packed_lag[order] = (np.cumsum(disk) - disk[0]) / (
        100.0 * half / REFERENCE_HALF * np.mean(pigment[4] ** 2)
    )
    area_shift = np.zeros_like(birth)
    receiving = None
    if ballistic and half > 6.*scale and _jet_slenderness(half)*half > scale:
        # Redistribute area along the receiving flight-time coordinate.
        # Averaging across an aperture radius relieves density contrasts
        # created where previously independent material streams merged.
        start = _supported_face(material.depth, head, packed_lag, half, entry)
        spacing = grid[1] - grid[0]
        bins = np.arange(0., grid[-1]+2.*spacing, spacing)
        area = np.histogram(start, bins=bins, weights=material.radius**2)[0]
        width = max(1, round(half / spacing))
        kernel = np.r_[1:width+1, width-1:0:-1].astype(float)
        smooth = np.convolve(np.pad(area, (width-1, width-1), mode="edge"),
                             kernel / kernel.sum(), mode="valid")
        mass = np.r_[0., np.cumsum(area)]
        target_mass = np.r_[0., np.cumsum(smooth)]
        target = np.interp(np.interp(start, bins, mass) * target_mass[-1] / mass[-1], target_mass, bins)
        ages, distances = _steady_flight_table(end, grid[-1] + 2.*half)
        area_shift = np.interp(target, distances, ages) - np.interp(start, distances, ages)
        first = material.serial < HEAD_ID_START // 2
        if first.any():
            boundary = max(0., min(material.depth[first].min() - half, head - 8.*half))
            residence = max(1.e-6, settled_clock[-1] - np.interp(grid[-1]-half, grid, settled_clock))
            receiving = ReceivingLayer(boundary, residence)
    opening = OpeningFlight(
        material,
        keys,
        birth,
        contact,
        entry,
        gravity,
        times,
        maps,
        grid,
        settled_clock,
        crossings,
        lip_shift,
        pressure,
        advance,
        unloading_time,
        packed_lag,
        area_shift,
        receiving,
    )

    if receiving is not None:
        opening = opening._replace(airborne=_free_flight_map(opening, half, end, floor, packed))
    return opening


def _supported_face(depth, nose, packed_lag, half, entry):
    """Limit excess axial dilation within the connected release packet."""
    lag = np.maximum(0., nose - depth)
    section = min(nose, 8. * half)
    if section <= 0.:
        return depth
    # The packed material may stretch as discharge accelerates. Once the
    # flight is established, contact transmits stress across neighbouring
    # layers; grains elsewhere in the column remain on their own trajectories.
    extension = np.maximum(0., lag - .85*half - packed_lag * max(1., 2.15 * B13_TEXTURE_SPEED / entry))
    extension += half * np.expm1(-extension / half)
    developed = np.clip((nose / half - 6.) / 3., 0., 1.)
    coupled = np.maximum(0., 1. - lag / section) ** 2
    return depth + .75 * developed**2 * (3.-2.*developed) * coupled * extension


def _free_flight_map(opening, half, end, floor, packed_charge):
    """Remove deceleration of airborne material between its release and impact."""
    layer = opening.receiving
    order = np.argsort(opening.material.depth)
    depth = opening.material.depth[order]
    grid = np.linspace(0., depth[-1], 513)

    def field(a):
        return np.interp(grid, depth, a[order])
    birth, advance, packed = map(field, (opening.birth, opening.advance, opening.packed_lag))
    entry, gravity = opening.entry, opening.gravity
    contact = opening.contact
    step = 1./120.
    ref_age, ref_depth = _steady_flight_table(end, floor+4.*half)
    last_flight = np.interp(floor+half, ref_depth, ref_age) / _birth_material()[6].min()
    times = contact + np.arange(-math.ceil(contact/step), math.ceil(last_flight/step)+2)*step
    path = []
    age0 = np.interp(grid, ref_depth, ref_age)
    clock = np.interp(grid, opening.grid, opening.settled_clock)
    join = np.clip((grid-layer.boundary)/half, 0., 1.)
    join *= join*(3.-2.*join)
    field_shift = field(opening.area_shift)
    for t in times:
        if t < opening.times[0]:
            elapsed = t-birth-_unloaded_fraction(t, opening.times[0], opening.unloading_time)*advance
            pos = elapsed * (entry + .5*gravity*np.maximum(0., elapsed))
        elif t < contact:
            i = max(0, np.searchsorted(opening.times,t)-1)
            f = (t-opening.times[i])/(opening.times[i+1]-opening.times[i])
            pos = np.interp(grid, opening.grid, opening.maps[i]+f*(opening.maps[i+1]-opening.maps[i]))
        else:
            dt = t-contact
            coast = -layer.residence*np.expm1(-dt/layer.residence)
            clock_now = clock+coast
            pos = np.interp(clock_now, opening.settled_clock, opening.grid)
            pos += (np.maximum(0., clock_now-opening.settled_clock[-1]) *
                    (opening.grid[-1]-opening.grid[-2])/(opening.settled_clock[-1]-opening.settled_clock[-2]))
        pos = _supported_face(pos, max(0., pos[-1]), packed, half, entry)
        if t >= contact:
            after = np.interp(pos, ref_depth, ref_age)
            recover = np.clip((dt-layer.residence)*entry/(2.*half),0.,1.)
            remain = min(1.,layer.residence/max(dt,layer.residence))*(1.-recover**2*(3.-2.*recover))
            blend = join+remain*(1.-join)
            pos = _pigment_depth(age0 + dt + blend*(after-age0-coast) +
                                 (-np.expm1(-(dt*entry/half)**2))*join*field_shift, end)
        path.append(pos)
    reference = np.asarray(path)
    path = reference.copy()
    # A grain reaching the floor no longer constrains the acceleration of
    # grains still in the air. Extend its unconstrained side past the floor.
    advance_speed = max(2.*floor/(contact-opening.crossings.min()), entry)
    for col in path.T:
        hit = col.searchsorted(floor)
        if 0 < hit < len(col):
            col[hit:] = col[hit] + advance_speed*(times[hit:]-times[hit])
    # Slopes on a free trajectory can only increase. Pool adjacent time
    # intervals where the proposed discharge/receiving motions would brake.
    fitted = np.empty_like(path)
    for n, col in enumerate(path.T):
        hull = []
        for k, y in enumerate(col):
            while len(hull) >= 2:
                a, b = hull[-2:]
                if (col[b]-col[a])*(k-b) <= (y-col[b])*(b-a):
                    break
                hull.pop()
            hull.append(k)
        fitted[:,n] = np.interp(times,times[hull],col[hull])
    # A connected release cannot thin faster than its actual settled
    # cross-section can uncover the next layer of disks at the mouth.
    charge = field(packed_charge-packed_charge.min())
    first = opening.crossings.min()
    gravity = max(0., 2.*(depth[-1]/(contact-first)-entry)/(contact-first))
    # The first exposed surface already has air between some neighbours.
    # Use its observed release phase to bound the packed support locally.
    departure = 2.*charge/(entry+np.sqrt(entry**2+2.*gravity*charge))
    opening_lag = np.maximum(0., field(opening.crossings)-first-departure)
    support_departure = departure + .5*opening_lag
    gravity_at_release = gravity*np.exp(-(charge/(3.*half))**2)
    travel = 2.*floor/(entry+np.sqrt(entry**2+2.*gravity_at_release*floor))
    # Each disk can leave only as its portion of the mouth is uncovered.
    # Fix both crossings in material order before integrating its path.
    departure = np.maximum(departure, field(opening.crossings)-first)
    flight = travel+support_departure-departure
    acceleration = np.maximum(0.,2.*(floor/flight-entry)/flight)
    elapsed = times[:,None] - first - departure
    carried = elapsed*(entry + .5*acceleration*np.maximum(0.,elapsed))
    free = np.clip((grid-layer.boundary)/(depth[-1]-layer.boundary),0.,1.)
    free = free*free*(3.-2.*free)
    fitted += free * np.maximum(0.,carried-fitted)
    # Attach a fixed flight coordinate to each material layer. At the end of
    # filling the empty glass, each section must contain the original disk
    # area. Invert the ordered flight field once at packing time. Neighbours
    # retain this coordinate for their entire flight.
    filled = np.searchsorted(times, contact)
    inverse = np.interp(path[filled], fitted[filled], grid)
    supply = np.clip((grid-layer.boundary)/half, 0., 1.)
    # The downstream anchor represents the established discharge. Ahead of
    # its last disk the free face is governed by its own packing alone.
    overlap = depth[opening.material.serial[order] >= HEAD_ID_START // 2].max(initial=0.)
    share = np.clip((overlap + half - grid) / half, 0., 1.)
    share *= share*(3.-2.*share)
    coordinates = grid + _jet_slenderness(half)*share*supply*(inverse-grid)
    fitted = np.vstack([np.interp(coordinates,grid,row) for row in fitted])
    # Neighbours in the packed throat share motion over the aperture. Keep
    # their disk-area weights fixed for the entire flight so support cannot
    # switch merely because the first disk has landed. Outside the glass,
    # extrapolate with the last airborne velocity before taking this mean.
    for col in fitted.T:
        hit = np.searchsorted(col, floor)
        if 1 < hit < len(times):
            speed = (col[hit-1] - col[hit-2]) / step
            col[hit:] = col[hit-1] + speed*(times[hit:]-times[hit-1])
    area = packed[::-1]
    span = np.minimum(2.*half, np.minimum(area-area[0], area[-1]-area))
    neighbours = area[:, None] + span[:, None] * np.linspace(-1., 1., 33)
    coordinate = np.interp(neighbours, area, grid[::-1])
    left = np.clip(np.searchsorted(grid, coordinate, side="right")-1, 0, len(grid)-2)
    fraction = (coordinate-grid[left]) / (grid[left+1]-grid[left])
    positions = np.array([(row[left] + fraction*(row[left+1]-row[left])).mean(axis=1)[::-1]
                          for row in fitted])
    # Pool adjacent sections only where projected area per unit travel time
    # increases downstream in the freely accelerating jet. Preserve both
    # endpoints and the total disk area in every pooled interval.
    section = positions[np.abs(times-contact).argmin()]
    lower = np.interp(layer.boundary, grid, section)
    edges = np.linspace(lower, section[-1], max(3, round((section[-1]-lower)/(half/2.)))+1)
    disk_depth = np.interp(opening.material.depth, grid, section)
    area = np.histogram(disk_depth, edges, weights=opening.material.radius**2)[0]
    clocks = 2.*edges / (entry + np.sqrt(entry**2 + 2.*gravity*edges))
    residence = np.diff(clocks)
    blocks = []
    for i, (mass, span) in enumerate(zip(area, residence)):
        start = i
        while blocks and mass * blocks[-1][2] > blocks[-1][1] * span:
            start, previous_mass, previous_span = blocks.pop()
            mass += previous_mass
            span += previous_span
        blocks.append((start, mass, span))
    target_area = area.copy()
    stop = len(area)
    for start, mass, span in reversed(blocks):
        target_area[start:stop] = residence[start:stop] * mass / span
        stop = start
    occupied = np.interp(section, edges, np.r_[0., np.cumsum(area)])
    target = np.where(section > lower, np.interp(occupied, np.r_[0., np.cumsum(target_area)], edges), section)
    # Near the original free surface the packed disks can separate in
    # proportion to the change in speed between the outlet and the floor.
    impact_speed = entry + gravity*(contact-first)
    support = np.exp(-charge / (2.*np.mean(opening.material.radius)))
    target += support * np.maximum(0., section[-1]-charge*impact_speed/entry-target)
    coordinate = np.interp(target, section, grid)
    positions = np.array([np.interp(coordinate, grid, row) for row in positions])
    # The final disks crossing the lip still share the neck's actual clock.
    # Match the two ends of this small material section without letting a
    # newly released grain pass one that is already in flight.
    lip = np.searchsorted(grid, .25*half)
    upstream = reference[:, lip:lip+1]
    downstream = np.searchsorted(grid, half)
    # In a narrow mouth the material represented by one aperture width can
    # still be upstream of the last established disks. Find a fixed section
    # whose far endpoint really stays ahead while the near endpoint is live.
    visible = (upstream[:, 0] >= -half) & (upstream[:, 0] <= floor)
    for row, near in zip(positions[visible], upstream[visible, 0]):
        downstream = max(downstream, min(len(grid)-1, np.searchsorted(row, near+grid[1])))

    span = reference[:, downstream:downstream+1] - upstream
    section = slice(lip, downstream)
    share = (reference[:, section]-upstream)/np.maximum(span, 1.e-12)
    positions[:, section] = upstream + share*(positions[:, downstream:downstream+1]-upstream)
    positions[:, :lip] = reference[:, :lip]
    return AirborneFlightGrid(times, grid, positions)


def _airborne_section(opening, time, material):
    times, grid, positions = opening.airborne
    i = np.clip(np.searchsorted(times,time)-1, 0, len(times)-2)
    f = np.clip((time-times[i])/(times[i+1]-times[i]), 0., 1.)
    return np.interp(material, grid, positions[i]+f*(positions[i+1]-positions[i]))


@lru_cache(maxsize=16)
def _steady_flight_table(end, reach):
    age = np.linspace(0., reach / B13_TEXTURE_SPEED + 1., 2049)
    return age, _pigment_depth(age, end)


def _opening_positions(time, opening, flow, first_exit_lift, half):
    mat = opening.material
    if time < opening.times[0]:
        unloaded = _unloaded_fraction(time, opening.times[0], opening.unloading_time)
        age = time - opening.birth - unloaded * opening.advance
        active = age >= 0.0
        if hasattr(flow, "seconds_per_pixel"):
            depth = flow.depth_at(flow.age_at(first_exit_lift) + np.maximum(age, 0.0)) - first_exit_lift
        else:
            depth = age * (opening.entry + 0.5 * opening.gravity * age)
    else:
        i = max(0, np.searchsorted(opening.times, time) - 1)
        mix = (time - opening.times[i]) / (opening.times[i + 1] - opening.times[i])
        positions = opening.maps[i] + mix * (opening.maps[i + 1] - opening.maps[i])
        depth = np.interp(mat.depth, opening.grid, positions)
        active = depth >= 0.0
    nose = np.max(depth, where=active, initial=0.0)
    if opening.airborne is not None:
        depth = _supported_face(depth, nose, opening.packed_lag, half, opening.entry)
        inlet = mat.depth <= .25*half
        free = _airborne_section(opening, time, mat.depth)
        depth = np.where(inlet, depth, free)
        active = np.where(inlet, active, free >= 0.)
        nose = np.max(depth, where=active, initial=0.)
    return depth, active, nose


class Discharge(NamedTuple):
    depth: np.ndarray
    clock: np.ndarray
    velocity: np.ndarray
    release_rate: float
    packed_fraction: float
    material_offset: int
    first_keys: np.ndarray
    first_births: np.ndarray
    first_lanes: np.ndarray
    startup_duration: float
    feed_times: np.ndarray
    feed_swept: np.ndarray
    convergence: float

    def birth_time(self, nominal):
        return (np.interp(nominal, self.feed_swept, self.feed_times)
                + np.maximum(0., nominal-self.feed_swept[-1]))

    def nominal_time(self, t):
        return (np.interp(t, self.feed_times, self.feed_swept)
                + max(0., t-self.feed_times[-1]))

    def depth_at(self, age):
        return (np.interp(age, self.clock, self.depth)
                + np.maximum(0., age-self.clock[-1])*self.velocity[-1])

    def mass_motion(self, transit, cutoff, time, age, lane, jitter, outlet, scale, birth):
        # Preserve the measured formation of the first pile. Carriers that
        # straddle its protected interval retain their position at its end.
        if cutoff is None or time <= cutoff:
            return transit.motion(age, lane, jitter, outlet, scale, birth=birth)
        if np.any(birth < cutoff):
            past = np.maximum(0., cutoff-birth)
            y, _ = transit.motion(past, lane, jitter, outlet, scale, birth=birth)
            distance = (outlet-y)/scale
            phase = np.interp(distance, self.depth, self.clock)
            phase += np.maximum(0., distance-self.depth[-1])/self.velocity[-1]
            age = np.where(birth < cutoff, phase+time-cutoff, age)
        depth = self.depth_at(age)
        return outlet-scale*depth, -scale*np.interp(age, self.clock, self.velocity)


def _discharge_x(depth, keys, half, scale, gap, packed_fraction):
    pigment = _birth_material()
    usual = _stream_x(depth, pigment[1][keys], keys, half, scale, gap, math.inf, gap, 0.)
    if packed_fraction == 0.:
        return usual
    # In capacity-limited glasses the occupied area is the area used by the
    # transport model. Map the persistent packed lanes into that cross-section.
    from grain_style import B13Transit
    packed = (pigment[1][keys] + pigment[9][keys]) * (half-.4*scale)
    packed *= np.sqrt(B13Transit.relative_area(depth))
    return usual + packed_fraction*(packed-usual)


@lru_cache(maxsize=4)
def _discharge(end, half, floor, lift, scale, seed, neck):
    # A worker may prepare the next seed while the UI reads the live neck.
    # opening() memoizes its last time and cross section on the instance.
    if neck is not None:
        from copy import copy
        neck = copy(neck)
    opening = _opening_material(end, half, floor, lift, scale, seed, neck)
    flow = hourglass_transit(end, half)
    # Sample while even the leading grains are airborne. A half-aperture of
    # clearance gives a local velocity independent of collision or heap height.
    fastest = flow.lane_factor(0., 1.)
    clearance_time = (flow.age_at(floor)-flow.age_at(max(0., floor-.5*half))) / fastest
    start = max(opening.times[0], opening.contact-clearance_time)
    step = min(1./240., (opening.contact-start)*.5)
    now, active, _ = _opening_positions(start, opening, flow, lift, half)
    previous, was_active, _ = _opening_positions(start-step, opening, flow, lift, half)
    airborne = active & was_active & (now >= 0.)
    order = np.argsort(now[airborne])
    z = now[airborne][order]
    speeds = ((now-previous)[airborne]/step)[order]
    spacing = max(2.*scale, .12*half)
    grid = np.linspace(0., floor+4.*scale, max(3, math.ceil((floor+4.*scale)/spacing)+1))
    left, right = (np.searchsorted(z, grid+offset) for offset in (-spacing, spacing))
    v = np.array([np.median(speeds[a:b]) if b>a else np.nan for a,b in zip(left,right)])
    valid = np.isfinite(v) & (v > 0.)
    if valid.any():
        v = np.interp(grid, grid[valid], v[valid])
    else:
        # A sub-grain opening may have no resolved velocity samples.
        v = np.maximum(1., np.gradient(grid)/np.gradient(flow.age_at(grid)))
    # In a wide, volume-limited mouth the dated first-impact mass carriers
    # also bound how slowly the visible disks can travel. Use the same
    # packed aperture and occupied area as their first free fall.
    # Use the transport law for this aperture. The pure capacity limit is
    # only reached for the short settings; applying it to blended openings
    # forces a velocity plateau halfway through their free fall.
    packed_speed = np.gradient(grid) / np.gradient(flow.age_at(grid))
    confinement = _jet_slenderness(half)
    v += (1.-confinement)*np.maximum(0., packed_speed-v)
    clock = np.r_[0., np.cumsum(.5*np.diff(grid)*(1./v[1:] + 1./v[:-1]))]
    pigment = _birth_material()
    packed_fraction = (1.-confinement)*(1.-getattr(flow, 'blend', 0.))
    # Continue the actual flux through the packed mouth. Disk area accounts
    # for different grain radii without adding grains to an existing layer.
    inlet_area = np.sum(opening.material.radius[active & (now >= 0.) & (now <= half)]**2)
    mean_area = scale**2*np.mean(pigment[4]**2)
    rate = inlet_area/(mean_area*np.interp(half, grid, clock))
    if rate <= 0.:
        rate = 100.*v[0]*half/(REFERENCE_HALF*scale**2)
    # Match the time of first contact in this geometry using a single
    # travel clock, including the very first disk that clears the lip.
    factor = np.interp(opening.material.depth.max(), grid, clock)/opening.contact
    clock /= factor
    v *= factor
    rate *= factor
    offset = _opening_pattern(seed)[0]
    # Resolve the packed face across the thickness of the round outlet.
    # Each slice contains hard disks supported by the actual floor and their
    # neighbours. Preserve their transverse positions once support is removed.
    sections = max(2, round(math.sqrt(half/scale)))
    diameter = 2.*scale*np.mean(pigment[4])
    slices = []
    exposed_area = layer_area = 0.
    for i in range(sections):
        through = (2.*i+1.)/sections-1.
        width = half*np.sqrt(1.-through**2)
        lanes, _, support, basal, disks = _section_face(
            width, scale, (int(seed or 0)+i) % (1 << 64))
        x, y, radii, keys = disks.T
        exposed_area += np.sum(radii[y <= radii+1.e-8]**2)
        layer_area += np.sum(radii[y-radii < diameter]**2)
        extra = (support-support.min())*diameter/max(diameter,support.mean())
        path = np.interp(x/width, lanes, basal+extra)
        radial_sq = (x/half)**2 + through**2
        shear_width = np.sqrt(half*diameter)
        plug = max(0., 1.-shear_width/half)
        path += confinement*shear_width*np.maximum(0.,radial_sq-plug**2)
        slices.append((y, path, x/half, keys))
    heights, paths, lanes, keys = np.concatenate([np.stack(row) for row in slices], axis=1)
    order = np.argsort(heights, kind="stable")
    # The calibrated flux sets the optical area per layer. Beyond the exposed
    # surface all sections are open and connect continuously to the usual bed.
    count = min(len(order), max(1, math.ceil(2.*max(diameter, paths.max())*rate/v[0])))
    ids = order[:count]
    thickness = count*v[0]/rate
    charge = (np.arange(count)+.5)*v[0]/rate
    available = charge + paths[ids]*(1.-charge/thickness)**2
    order = np.argsort(available, kind="stable")
    first_keys = keys[ids[order]].astype(np.intp)
    lane_order = np.argsort(pigment[1]+pigment[9])
    first_lanes = lane_order[np.clip(((lanes[ids[order]]+1.)*.5*BLOCK).astype(int),0,BLOCK-1)]
    first = available[order[0]]
    fraction = (available[order]-first)/(thickness-first)
    first_births = (.5 + fraction*(count-.5))/rate

    acceleration = v[0]**2 / half
    exposed = exposed_area/layer_area
    base_flux = .5*(1.-confinement)*exposed
    surface_flux = (2./3.)*np.sqrt(2.*acceleration*diameter)/v[0]
    startup_duration = (1.-base_flux)*v[0]/acceleration
    times = np.linspace(0., startup_duration, 257)
    inlet = base_flux + times*acceleration/v[0]
    # In the confined layer only grains connected to the free face can
    # initially leave. More of the packed cross-section unloads as the bed
    # advances. Integrate moving area times inlet speed, in steady-flux units.
    swept_length = times*(base_flux*v[0]+.5*acceleration*times)
    moving_area = 1.-confinement*(1.-exposed)*(1.-swept_length/swept_length[-1])
    flux = np.maximum(surface_flux, inlet*moving_area)
    swept = np.r_[0., np.cumsum(.5*(flux[1:]+flux[:-1])*np.diff(times))]
    convergence = neck.outlet_slope(diameter*neck.scale) if neck else 0.
    return Discharge(grid, clock, v, rate, packed_fraction, offset, first_keys, first_births, first_lanes,
                     startup_duration, times, swept, convergence)


def sample_grains(time, end, half, gap, grain_scale=1., max_depth=None, stride=1,
                  inlet_lift=0., *, opening_seed=0, first_exit_lift=0.,
                  floor_depth=2.*REFERENCE_RADIUS, neck=None, landed=None):
    discharge = _discharge(end, half, floor_depth, first_exit_lift, grain_scale,
                           opening_seed, neck)
    limit = (gap if max_depth is None else max_depth) + 2.*grain_scale
    lifetime = np.interp(limit, discharge.depth, discharge.clock)
    lifetime += max(0., limit-discharge.depth[-1])/discharge.velocity[-1]
    pigment = _birth_material()
    rate = discharge.release_rate
    # Invert the measured first-layer release times on either side of
    # the visible age range. Regular feed uses the identical outlet rate.
    prefix = len(discharge.first_births)
    minimum, maximum = time-lifetime, min(time, end)
    start, stop = discharge.nominal_time(minimum), discharge.nominal_time(maximum)
    lo = (np.searchsorted(discharge.first_births, start) if start < prefix/rate
          else max(prefix, math.floor(start*rate)))
    hi = (np.searchsorted(discharge.first_births, stop, side="right") if stop < prefix/rate
          else max(prefix, math.ceil(stop*rate)))
    lo += (-lo) % stride
    serial = np.arange(lo, hi, stride, dtype=np.int64)
    birth = (serial+.5)/rate
    front = serial < prefix
    birth[front] = discharge.first_births[serial[front]]
    birth = discharge.birth_time(birth)
    keep = (birth <= maximum) & (birth >= minimum)
    serial, birth = serial[keep], birth[keep]
    keys = (discharge.material_offset + serial % BLOCK + (serial // BLOCK)*24317) % BLOCK
    front = serial < len(discharge.first_keys)
    keys[front] = discharge.first_keys[serial[front]]
    depth = discharge.depth_at(time-birth)
    lane_keys = keys.copy() if np.any(front) else keys
    lane_keys[front] = discharge.first_lanes[serial[front]]
    x = _discharge_x(depth, lane_keys, half, grain_scale, gap, discharge.packed_fraction)
    if discharge.convergence and birth.size and birth[0] < discharge.startup_duration:
        # The confined bed approaches the opening along the converging wall.
        # The free leading layer retains that inward component; following
        # grains lose it as the onset of discharge relieves the load.
        speed0 = discharge.velocity[0]
        speed = np.interp(depth, discharge.depth, discharge.velocity)
        packed_radius = np.minimum(1., np.sqrt(speed0/speed))
        loaded = np.maximum(0., 1.-birth/discharge.startup_duration)
        inward = 1.-np.exp(-discharge.convergence*speed0*(time-birth)/half)
        x *= 1. - loaded*inward*(1.-packed_radius)
    return Grains(serial, x, depth, grain_scale*pigment[4][keys], pigment[3][keys])


VERTEX_SHADER = """
#ifdef GL_ES
precision highp float;
#endif
// The same two attributes as an ordinary Kivy mesh, with extra components.
attribute vec4 vPosition;    // pixel position and local disc offset
attribute vec3 vTexCoords0;  // support radius, pigment and surface height
varying vec4 frag_color;
varying vec2 tex_coord0;
uniform vec4 jet;  // centre x, outlet y, glass scale, reference half-width
uniform vec4 target;  // native-pixel bounds of the optical accumulation texture
varying vec2 local_offset;
varying vec2 jet_position;
varying vec3 grain;
void main(void) {
    local_offset = vPosition.zw;
    jet_position = vec2(vPosition.x-jet.x, jet.y-vPosition.y) / jet.z;
    grain = vec3(vTexCoords0.xy, (vPosition.y-vTexCoords0.z) / jet.z);
    gl_Position = vec4(2.0*(vPosition.xy-target.xy)/target.zw-1.0, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = """
$HEADER$
uniform vec4 jet;
varying vec2 local_offset;
varying vec2 jet_position;
varying vec3 grain;
void main(void) {
    float depth = jet_position.y;
    if (grain.z < 0.0 || (depth < 0.0 && abs(jet_position.x) > jet.w)) discard;
    float ink = clamp(grain.x - length(local_offset), 0.0, .86);
    float extinction = 1.65 * (2.0 - smoothstep(4.0, 43.0, depth));
    // R and G accumulate the positive and negative portions of the same dye.
    // One exposure per pixel keeps both sums in RGBA8 range even at the lip.
    float inner = max(0.0, 1.0 - abs(jet_position.x)/jet.w);
    float range = 5.0 + 72.0 * inner / (1.0 + .10 * max(0.0, depth));
    vec2 weights = .5 * ink / range * vec2(1.0+grain.y, 1.0-grain.y);
    gl_FragColor = vec4(weights, 0.0, 1.0-exp(-extinction*ink));
}
"""

COMPOSITE_VERTEX = """
$HEADER$
uniform vec4 jet;
varying vec2 jet_position;
void main(void) {
    jet_position = vec2(vPosition.x-jet.x, jet.y-vPosition.y) / jet.z;
    tex_coord0 = vTexCoords0;
    frag_color = vec4(1.0, 1.0, 1.0, opacity);
    gl_Position = projection_mat * modelview_mat * vec4(vPosition, 0.0, 1.0);
}
"""

COMPOSITE_FRAGMENT = """
$HEADER$
uniform vec4 jet;
uniform vec3 pigment;
uniform float gap;
varying vec2 jet_position;
void main(void) {
    vec4 sample_value = texture2D(texture0, tex_coord0);
    float sum = sample_value.r + sample_value.g;
    float tone = (sample_value.r-sample_value.g) / max(sum, 1.0/255.0);
    float inner = max(0.0, 1.0 - pow(abs(jet_position.x)/(.75*jet.w), 2.0));
    float body = .48 * inner * inner * clamp((jet_position.y-38.0)/150.0, 0.0, 1.0)
                              * clamp((gap-jet_position.y-11.0)/27.0, 0.0, 1.0);
    float alpha = sample_value.a;
    alpha += (1.0-alpha) * alpha * body;
    gl_FragColor = vec4(pigment + tone * vec3(18.0,20.0,17.0)/255.0, alpha * frag_color.a);
}
"""


class StreamArea:
    def __init__(self, widget):
        scale = widget._R_inner / REFERENCE_RADIUS
        outlet = 2. * widget._neck_y - widget._taper["y_bot"]
        mouth = widget._taper["t_in"]
        # The offscreen image must include the airborne grains on either side.
        extent = mouth * (1.65 + 1.9*_jet_slenderness(mouth/scale)) + 8. * scale
        left = math.floor(widget._cx - extent)
        bottom = math.floor(widget._lower_sand_bot - 2. * scale)
        size = (math.ceil(widget._cx + extent) - left, math.ceil(outlet + 9. * scale) - bottom)
        self.surface_x = np.arange(left, left + size[0] + 1, dtype=float)
        dx = self.surface_x - widget._cx
        self.bowl = widget._lower_y_c - np.sqrt(np.maximum(0., widget._R_inner**2 - dx**2))
        self.origin = (left, bottom)
        self.size = size
        self.jet = (widget._cx, outlet, scale, widget._taper["t_in"] / scale)


class StreamMesh(StreamArea):
    """One native-resolution disc per visible grain, in reusable GLES2 batches."""
    chunk = 4096
    corners = np.array([[-1., -1.], [1., -1.], [1., 1.], [-1., 1.]], dtype=np.float32)

    def __init__(self, widget):
        from kivy.graphics import Callback, ClearBuffers, ClearColor, Fbo, Rectangle, RenderContext
        from kivy.graphics.opengl import (GL_ONE, GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA,
                                          glBlendFuncSeparate)
        super().__init__(widget)
        self.context = RenderContext(use_parent_projection=True, use_parent_modelview=True)
        self.context.shader.vs = COMPOSITE_VERTEX
        self.context.shader.fs = COMPOSITE_FRAGMENT
        with self.context:
            self.field = Fbo(size=self.size)
            Rectangle(pos=self.origin, size=self.size, texture=self.field.texture)
        self.field.shader.vs = VERTEX_SHADER
        self.field.shader.fs = FRAGMENT_SHADER
        if not self.context.shader.success or not self.field.shader.success:
            raise RuntimeError("B13 grain shader could not compile")
        self.field["target"] = tuple(map(float, self.origin + self.size))
        self.context["jet"] = self.field["jet"] = self.jet
        with self.field.before:
            ClearColor(0., 0., 0., 0.)
            ClearBuffers()
            Callback(lambda _: glBlendFuncSeparate(GL_ONE, GL_ONE, GL_ONE, GL_ONE_MINUS_SRC_ALPHA))
        with self.field.after:
            Callback(lambda _: glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA,
                                                   GL_ONE, GL_ONE_MINUS_SRC_ALPHA))
        self.indices = (4 * np.arange(self.chunk, dtype=np.uint16)[:, None]
                        + np.array([0, 1, 2, 0, 2, 3], dtype=np.uint16)).ravel()
        self.batches = []

    def add_batch(self):
        from kivy.graphics import Mesh

        vertices = np.zeros((self.chunk, 4, 7), dtype=np.float32)
        with self.field:
            mesh = Mesh(vertices=vertices.ravel(), indices=[], mode="triangles",
                        fmt=[(b"vPosition", 4, "float"), (b"vTexCoords0", 3, "float")])
        self.batches.append((mesh, vertices))

    def update(self, grains, surface_y, pigment, gap):
        cx, outlet, scale, _ = self.jet
        self.context["gap"] = float(gap)
        self.context["pigment"] = pigment
        count = len(grains.x)
        while count > self.chunk * len(self.batches):
            self.add_batch()
        for i, (mesh, buf) in enumerate(self.batches):
            first = self.chunk * i
            n = min(self.chunk, max(0, count - first))
            if n == 0:
                if len(mesh.indices):
                    mesh.indices = []
                continue
            block = slice(first, first + n)
            v = buf[:n]
            support = grains.radius[block] + .48
            v[:, :, 2:4] = support[:, None, None] * self.corners
            v[:, :, 0] = (cx + scale * grains.x[block, None]) + scale * v[:, :, 2]
            v[:, :, 1] = (outlet - scale * grains.depth[block, None]) + scale * v[:, :, 3]
            v[:, :, 4] = support[:, None]
            v[:, :, 5] = grains.tone[block, None]
            v[:, :, 6] = np.interp(v[:, :, 0], self.surface_x, surface_y)
            # Publish data before its indices. Keep the backing size constant
            # across first draw, batch growth and shrinking tails.
            mesh.vertices = buf.ravel()
            if len(mesh.indices) != n * 6:
                mesh.indices = self.indices[:n * 6]

    def clear(self):
        for mesh, _ in self.batches:
            if len(mesh.indices):
                mesh.indices = []


class TextureStream(StreamArea):
    """Render the B13 optical image on the CPU and display one ordinary rectangle."""
    def __init__(self, widget):
        from kivy.graphics import Color, InstructionGroup, PopState, PushState, Rectangle
        from kivy.graphics.texture import Texture
        from b13_raster import GrainImage

        super().__init__(widget)
        self.image = GrainImage(self.origin, self.size, self.jet)
        self.source_count = 0
        self.texture = Texture.create(size=self.size, colorfmt="rgba")
        self.texture.add_reload_observer(self.upload)
        self.upload(self.texture)
        self.context = InstructionGroup()
        self.context.add(PushState("color"))
        self.context.add(Color(1., 1., 1., 1.))
        self.context.add(Rectangle(pos=self.origin, size=self.size, texture=self.texture))
        self.context.add(PopState("color"))

    def upload(self, texture):
        texture.blit_buffer(self.image.rgba.ravel(), colorfmt="rgba", bufferfmt="ubyte")

    def update(self, grains, surface_y, pigment, gap):
        if not len(grains.x):
            self.clear()
            return
        self.image.paint(grains, surface_y, pigment, gap)
        self.upload(self.texture)
        self.source_count = len(grains.x)
        self.context.flag_update()

    def clear(self):
        if self.source_count:
            self.image.rgba.fill(0)
            self.upload(self.texture)
            self.source_count = 0
            self.context.flag_update()


def stream_for_renderer(renderer):
    if isinstance(renderer, bytes):
        renderer = renderer.decode("ascii", "replace")
    name = "".join((renderer or "").lower().split()).replace("(tm)", "")
    return TextureStream if name == "adreno840" else StreamMesh


class LineStream(StreamArea):
    """Use the existing emergency line pools with short optical strokes.

    One stroke covers several grain footprints. Keep a fixed subset of optical
    identities across frames so the mass carriers cannot draw long vertical bars.
    """
    stride = 3

    def __init__(self, widget):
        super().__init__(widget)
        self.pools = widget._stream_pools
        self.counts = widget._stream_counts

    def clear(self):
        for key, count in self.counts.items():
            for line in self.pools[key][2][:count]:
                line.points = []
            self.counts[key] = 0

    def update(self, grains, surface_y, pigment, gap):
        from kivy.graphics import Line
        cx, lip, scale, _ = self.jet
        x = cx + scale * grains.x
        y = lip - scale * grains.depth
        floor = np.interp(x, self.surface_x, surface_y)
        width = np.maximum(1., scale * grains.radius)
        length = self.stride * np.pi * (scale * grains.radius)**2 / (2. * width)
        top = np.minimum(lip, y + .5 * length)
        bottom = np.maximum(floor, y - .5 * length)
        shades = np.rint(5. + 2. * grains.tone).astype(int)
        for shade in range(3, 8):
            key = (shade, 1)
            group, _, pool = self.pools[key]
            ids = np.flatnonzero((shades == shade) & (top > bottom))
            for i, j in enumerate(ids):
                coords = (x[j], bottom[j], x[j], top[j])
                if i == len(pool):
                    line = Line(points=coords, width=width[j], cap="none")
                    group.add(line)
                    pool.append(line)
                else:
                    pool[i].width = width[j]
                    pool[i].points = coords
            for line in pool[len(ids):self.counts[key]]:
                line.points = []
            self.counts[key] = len(ids)


def install(widget_class):
    from short_grain_stream import mound_surface_at
    old_build, old_draw = widget_class._build_dynamic_canvas, widget_class._draw_stream

    def use_lines(w):
        if w._b13_mesh is not None:
            w.canvas.remove(w._b13_mesh.context)
        w._b13_mesh = w._short_flow_context = None
        w._short_material_mode = None
        w._short_flow_state = {}
        w._reserve_stream_lines()
        w.flow_renderer = "line_pool"

    def build(w):
        w._b13_lines = None
        w._b13_lines_failed = False
        w._stream_batched = True
        try:
            old_build(w)
        finally:
            w._stream_batched = False
        w._b13_mesh = w._short_flow_context = None
        w._short_material_mode = None
        if getattr(w, "_b13_draw_failed", False):
            use_lines(w)
            return
        try:
            if os.environ.get("HG_FLOW_FORCE_FAIL"):
                raise RuntimeError("forced B13 fallback")
            from kivy.graphics.opengl import GL_RENDERER, glGetString
            stream = stream_for_renderer(glGetString(GL_RENDERER))(w)
            _birth_material()  # Prepare the grain identities before the first timed frame.
            # Packed sand and the original impact/splash layers occlude the jet.
            front = w._sand_flow_contexts[1] if w._sand_flow_contexts else w._splash_group
            w.canvas.insert(w.canvas.children.index(front), stream.context)
        except Exception as exc:
            use_lines(w)
            print("B13 unavailable, using Line: %s" % exc)
            return
        w._b13_mesh = stream
        w._short_flow_context = stream.context
        w._short_material_mode = "b13"
        w.flow_renderer = "b13_grains"

    def draw_grains(w, stream):
        game = sys.modules[widget_class.__module__]
        radius = w._R_inner
        scale = radius / REFERENCE_RADIUS
        half = w._taper["t_in"] / scale
        outlet = 2. * w._neck_y - w._taper["y_bot"]
        gap = (outlet - w.get_mound_top_y()) / scale
        time = w.elapsed + w._stream_settle_age
        end = w.duration
        grain_scale = (game.SAND_MATERIAL_COARSE / 1.8) ** .5
        opened = w._sand_active and time > 0.
        if not opened:
            stream.clear()
            w._short_flow_state = dict(active=False, particles=w.pn, rendered_particles=0,
                                       material_mode="b13", pitch=scale * grain_scale, position_style="b13")
            return
        surface_x = stream.surface_x
        surface_y = np.maximum(stream.bowl, mound_surface_at(surface_x, w._mound_carve._v, w._lower_sand_bot))
        deepest = (outlet - surface_y.min()) / scale
        grains = sample_grains(time, end, half, gap, grain_scale, deepest,
                               getattr(stream, "stride", 1), 0., opening_seed=w._opening_seed,
                               first_exit_lift=w._sand_neck.first_exit_lift/scale,
                               floor_depth=(outlet-w._lower_sand_bot)/scale,
                               neck=w._sand_neck if hasattr(w._sand_neck, "scale") else None,
                               landed=(w._sand_landed > 1.e-12 if hasattr(w, "_sand_landed") else None))
        pigment = (w.sand_base[0]-1./255., w.sand_base[1]-1./255., w.sand_base[2])
        stream.update(grains, surface_y, pigment, gap)
        w._short_flow_state = dict(active=bool(len(grains.x)), particles=w.pn,
                                   rendered_particles=len(grains.x), material_mode="b13",
                                   pitch=scale * grain_scale, position_style="b13")

    def draw(w):
        if w._b13_mesh is not None:
            try:
                return draw_grains(w, w._b13_mesh)
            except Exception:
                logging.getLogger(__name__).exception("B13 draw failed at %.3f s", w.elapsed)
                w._b13_draw_failed = True
                use_lines(w)
        return old_draw(w)

    def draw_lines(w):
        if w._b13_lines is None:
            w._b13_lines = LineStream(w)
        return draw_grains(w, w._b13_lines)

    widget_class._build_dynamic_canvas = build
    widget_class._draw_stream = draw
    widget_class._draw_b13_lines = draw_lines
