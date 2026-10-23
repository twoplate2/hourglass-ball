"""Period-to-aperture geometry, measured inside the glass wall."""
import math

MAX_RATIO=.15
MIN_RATIO=.03
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
    u=max(0.0,min(1.0,math.log(max(WIDE_PERIOD,period)/WIDE_PERIOD)
                   /math.log(NARROW_PERIOD/WIDE_PERIOD)))
    return diameter*MAX_RATIO*(MIN_RATIO/MAX_RATIO)**u


def outer_half(period,diameter,wall):
    return inner_width(period,diameter)*.5+wall


def half_limits(diameter,wall):
    return diameter*MIN_RATIO*.5+wall,diameter*MAX_RATIO*.5+wall
