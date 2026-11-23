"""Period-to-aperture geometry, measured inside the glass wall."""
import math

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
