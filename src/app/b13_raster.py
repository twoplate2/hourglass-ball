"""Native-pixel B13 disc coverage for a regular RGBA texture. No GL calls."""
import math

import numpy as np


class GrainImage:
    def __init__(self, origin, size, jet):
        self.left, self.bottom = origin
        self.width, height = size
        self.cx, self.outlet, self.scale, half = jet
        self.rgba = np.zeros((height, self.width, 4), dtype=np.uint8)
        # Texture rows and Kivy coordinates both run from bottom to top.
        self.y = self.bottom + np.arange(height, dtype=np.float32) + .5
        self.depth = (self.outlet - self.y) / self.scale
        x = (self.left + np.arange(self.width, dtype=np.float32) + .5 - self.cx) / self.scale
        self.inside_lip = np.abs(x) <= half
        self.core = .48 * np.maximum(0., 1. - (x / (.75 * half)) ** 2) ** 2
        t = np.clip((self.depth - 4.) / 39., 0., 1.)
        self.extinction = 1.65 * (2. - t * t * (3. - 2. * t))

    def paint(self, grains, surface_y, pigment, gap):
        self.rgba.fill(0)
        if not len(grains.x):
            return
        scale = self.scale
        x = (self.cx - self.left + scale * grains.x).astype(np.float32)
        y = (self.outlet - self.bottom - scale * grains.depth).astype(np.float32)
        support = (grains.radius + .48).astype(np.float32)
        reach = math.ceil(float(support.max()) * scale)
        low = max(0, math.floor(float(y.min())) - reach)
        high = min(len(self.y), math.floor(float(y.max())) + reach + 1)
        if high <= low:
            return
        total = np.zeros((high - low, self.width), dtype=np.float32)
        dye = np.zeros_like(total)
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
            ink *= grains.tone[part, None, None].astype(np.float32)
            dye[rows] += np.bincount(cells, ink.ravel(), minlength=length).reshape(shape)

        rows = slice(low, high)
        depth = self.depth[rows, None]
        surface = .5 * (surface_y[:-1] + surface_y[1:])
        tone = np.divide(dye, total, out=dye, where=total > 0.)
        alpha = -np.expm1(-self.extinction[rows, None] * total)
        body = np.clip((depth - 38.) / 150., 0., 1.) * np.clip((gap - depth - 11.) / 27., 0., 1.)
        alpha += (1. - alpha) * alpha * body * self.core
        alpha *= (self.y[rows, None] >= surface) & ((depth >= 0.) | self.inside_lip)
        image = self.rgba[rows]
        image[:, :, 3] = np.rint(255. * alpha)
        for channel, shade in enumerate((18., 20., 17.)):
            image[:, :, channel] = np.clip(np.rint(255. * pigment[channel] + shade * tone), 0., 255.)
