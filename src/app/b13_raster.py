"""Native-pixel B13 disc coverage for a regular RGBA texture. No GL calls."""
import math
import ctypes
from functools import lru_cache

import numpy as np


def _configure_accumulator(library):
    vector = np.ctypeslib.ndpointer(dtype=np.float32, ndim=1, flags="C_CONTIGUOUS")
    plane = np.ctypeslib.ndpointer(dtype=np.float32, ndim=2, flags=("C_CONTIGUOUS", "WRITEABLE"))
    accumulate = library.b13_accumulate
    accumulate.argtypes = [ctypes.c_int, vector, vector, vector, vector, ctypes.c_float,
                           ctypes.c_int, ctypes.c_int, ctypes.c_int, plane, plane]
    accumulate.restype = None
    compose = library.b13_composite
    lip = np.ctypeslib.ndpointer(dtype=np.bool_, ndim=1, flags="C_CONTIGUOUS")
    surface = np.ctypeslib.ndpointer(dtype=np.float64, ndim=1, flags="C_CONTIGUOUS")
    image = np.ctypeslib.ndpointer(dtype=np.uint8, ndim=3, flags=("C_CONTIGUOUS", "WRITEABLE"))
    compose.argtypes = [ctypes.c_int, ctypes.c_int, plane, plane, vector, vector, vector,
                        vector, lip, ctypes.c_int, surface, ctypes.c_float, ctypes.c_float,
                        ctypes.c_float, image]
    compose.restype = None
    return accumulate, compose


@lru_cache(maxsize=1)
def _load_accumulator():
    try:
        return _configure_accumulator(ctypes.CDLL("libb13raster.so"))
    except (OSError, AttributeError):
        return None


class GrainImage:
    def __init__(self, origin, size, jet):
        self.left, self.bottom = origin
        self.width, height = size
        self.cx, self.outlet, self.scale, half = jet
        self.rgba = np.zeros((height, self.width, 4), dtype=np.uint8)
        self.accumulate, self.compose = _load_accumulator() or (None, None)
        self.coverage = np.empty((height, self.width), dtype=np.float32)
        self.dye = np.empty_like(self.coverage)
        # Texture rows and Kivy coordinates both run from bottom to top.
        self.y = self.bottom + np.arange(height, dtype=np.float32) + .5
        self.depth = (self.outlet - self.y) / self.scale
        x = (self.left + np.arange(self.width, dtype=np.float32) + .5 - self.cx) / self.scale
        self.inside_lip = np.abs(x) <= half
        self.core = .48 * np.maximum(0., 1. - (x / (.75 * half)) ** 2) ** 2
        t = np.clip((self.depth - 4.) / 39., 0., 1.)
        self.extinction = 1.65 * (2. - t * t * (3. - 2. * t))
        self.above_outlet = int(np.searchsorted(self.y, self.outlet, side="right"))
        self.body_depth = np.clip((self.depth - 38.) / 150., 0., 1.)

    def paint(self, grains, surface_y, pigment, gap):
        self.rgba.fill(0)
        if not len(grains.x):
            return
        count = len(grains.x)
        if (surface_y.shape != (self.width + 1,) or
                any(field.shape != (count,) for field in (grains.x, grains.depth, grains.radius, grains.tone))):
            raise ValueError("B13 grain attributes or surface do not match the raster dimensions")
        scale = self.scale
        x = (self.cx - self.left + scale * grains.x).astype(np.float32)
        y = (self.outlet - self.bottom - scale * grains.depth).astype(np.float32)
        support = (grains.radius + .48).astype(np.float32)
        reach = math.ceil(float(support.max()) * scale)
        low = max(0, math.floor(float(y.min())) - reach)
        high = min(len(self.y), math.floor(float(y.max())) + reach + 1)
        if high <= low:
            return
        total, dye = self.coverage[low:high], self.dye[low:high]
        total.fill(0.)
        dye.fill(0.)
        if self.accumulate is not None:
            self.accumulate(len(x), x, y, support, np.ascontiguousarray(grains.tone, dtype=np.float32),
                            scale, self.width, low, high, total, dye)
        else:
            self._accumulate_numpy(x, y, support, grains.tone, low, high, reach, total, dye)

        rows = slice(low, high)
        depth = self.depth[rows]
        surface = .5 * (surface_y[:-1] + surface_y[1:])
        body = self.body_depth[rows] * np.clip((gap - depth - 11.) / 27., 0., 1.)
        image = self.rgba[rows]
        if self.compose is not None:
            self.compose(self.width, high - low, total, dye, self.y[rows], self.extinction[rows],
                         body, self.core, self.inside_lip, self.above_outlet - low,
                         np.ascontiguousarray(surface, dtype=np.float64),
                         255.*pigment[0], 255.*pigment[1], 255.*pigment[2], image)
            return
        tone = np.divide(dye, total, out=dye, where=total > 0.)
        alpha = -np.expm1(-self.extinction[rows, None] * total)
        alpha += (1. - alpha) * alpha * body[:, None] * self.core
        alpha *= (self.y[rows, None] >= surface) & ((depth[:, None] >= 0.) | self.inside_lip)
        image[:, :, 3] = np.rint(255. * alpha)
        for channel, shade in enumerate((18., 20., 17.)):
            image[:, :, channel] = np.clip(np.rint(255. * pigment[channel] + shade * tone), 0., 255.)

    def _accumulate_numpy(self, x, y, support, tones, low, high, reach, total, dye):
        scale = self.scale
        offsets = np.arange(-reach, reach + 1, dtype=np.int32)
        # Bound the temporary footprint even at large native sizes and coarse grain settings.
        batch = max(1, 180000 // (len(offsets) ** 2))
        for start in range(0, len(x), batch):
            part = slice(start, start + batch)
            gx, gy = x[part, None], y[part, None]
            px = np.floor(gx).astype(np.int32) + offsets
            py = np.floor(gy).astype(np.int32) + offsets
            dx2 = ((px.astype(np.float32) + .5 - gx) / scale) ** 2
            dy2 = ((py.astype(np.float32) + .5 - gy) / scale) ** 2
            ink = np.sqrt(dy2[:, :, None] + dx2[:, None, :])
            np.subtract(support[part, None, None], ink, out=ink)
            np.clip(ink, 0., .86, out=ink)
            ink *= ((px >= 0) & (px < self.width))[:, None, :]
            ink *= ((py >= low) & (py < high))[:, :, None]
            np.clip(px, 0, self.width - 1, out=px)
            np.clip(py, low, high - 1, out=py)
            row = int(py.min())
            stop = int(py.max()) + 1
            cells = ((py - row)[:, :, None] * self.width + px[:, None, :]).ravel()
            rows = slice(row - low, stop - low)
            shape = (stop - row, self.width)
            length = shape[0] * shape[1]
            total[rows] += np.bincount(cells, ink.ravel(), minlength=length).reshape(shape)
            ink *= tones[part, None, None].astype(np.float32)
            dye[rows] += np.bincount(cells, ink.ravel(), minlength=length).reshape(shape)
