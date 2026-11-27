"""Review original gold-sand frames from capture_stream_motion.py on a hidden PC."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def gold(rgb):
    r, g, b = rgb.astype(np.int16).transpose(2, 0, 1)
    return (r - b > 45) & (r - g > 14)


def frame_image(folder, frame):
    with Image.open(folder / frame["file"]) as image:
        return np.asarray(image.convert("RGB"))


def crown_gap(rgb, geometry):
    cx = round(geometry["cx"])
    top = max(0, int(np.ceil(geometry["crown_y"])))
    column = gold(rgb)[top:, cx-1:cx+2].sum(axis=1) >= 2
    rows = np.flatnonzero(column)
    return int(rows[0]) if len(rows) else len(column)


def sphere_endpoint(rgb, geometry, upper):
    """Measure the neck-facing arc of a full sphere across both shoulders."""
    mask = gold(rgb)
    cx, radius = geometry["cx"], geometry["radius"]
    cy = geometry["crown_y"] + radius if upper else geometry["surface_y"] - radius
    lip = round(geometry["outlet_y"])
    # Both sides of the lip are visible at rest. Include the full neck so that
    # a cap or a flat plug attached to an otherwise round bulb is detected.
    region = mask[:lip+1] if upper else mask[lip:]
    x = np.arange(round(cx-.80*radius), round(cx+.80*radius)+1)
    opposite = mask[lip+1:] if upper else mask[:lip]
    if opposite[:, x].any():
        raise ValueError("静止端点在应当留空的一侧仍有沙粒")
    heights = (lip - region[::-1, x].argmax(axis=0) if upper
               else lip + region[:, x].argmax(axis=0))
    arc = np.sqrt(np.maximum(0., radius**2-(x+.5-cx)**2))
    error = np.abs(heights+.5 - (cy+arc if upper else cy-arc))
    if (not np.all(region[:,x].any(axis=0)) or error.max() > max(3., .014*radius)):
        where = "开场上杯" if upper else "落定下杯"
        raise ValueError(f"{where}偏离圆弧 {error.max():.1f} 像素")
    return float(error.max())


def primed_reservoir(rgb, geometry):
    """Check the resting charge against the visible glass and its original inventory."""
    sand = gold(rgb)
    height, width = sand.shape
    cx, radius = geometry["cx"], geometry["radius"]
    lip = round(geometry["outlet_y"])
    x = np.arange(width)+.5-cx
    y = np.arange(height)+.5-(geometry["crown_y"]+radius)
    if sand[lip:, abs(x) <= radius].any():
        raise ValueError("待机时已有沙越过颈口")
    # Follow the inner faces of the actual grey glass. The whole throat
    # and both shoulders should be filled from the equator to the outlet.
    wall = (rgb.max(axis=2) < 180) & (np.ptp(rgb.astype(int), axis=2) < 55)
    center = round(cx)
    rows = np.flatnonzero((y >= 0.) & (np.arange(height) < lip))
    for row in rows:
        left = np.flatnonzero(wall[row, :center])
        right = np.flatnonzero(wall[row, center:])
        if not left.size or not right.size:
            raise ValueError("无法辨认待机上杯的实际内壁")
        lo = min(center, left[-1]+3)
        hi = max(center+1, center+right[0]-2)
        if sand[row, lo:hi].mean() < .97:
            raise ValueError(f"待机填颈不连续，原图第 {row} 行有空隙")
    # The idle reference touches the inner crown across the visible upper arc.
    # Measure several columns so a pin of gold in the middle cannot mask a void.
    observed = crown_gap(rgb, geometry)
    cols = np.flatnonzero(abs(x) <= .25*radius)
    surface = sand[:lip, cols]
    arc = geometry["crown_y"] + radius - np.sqrt(radius**2 - x[cols]**2)
    # Compressed device frames can put the actual inner edge between two
    # pixel centres. Locate the glass per column where the rim is visible.
    yy = np.arange(lip)[:, None]
    rim = wall[:lip, cols] & (abs(yy-arc) <= max(3., .015*radius))
    edge = np.where(rim.any(axis=0), np.where(rim, yy+1, -1).max(axis=0), arc-.5)
    error = np.where(surface.any(axis=0), surface.argmax(axis=0)-edge, radius)
    if np.max(error) > max(2., .004*radius):
        raise ValueError(f"待机顶部空腔，沙面距内弧最多 {np.max(error):.1f} 原像素")
    return observed


def check_filled_lip_seam(mask, geometry):
    """A moving jet must stay connected through the last filled throat rows."""
    cx, lip = round(geometry["cx"]), round(geometry["outlet_y"])
    reach = max(1, round(.7*geometry["half_width"]))
    rows = mask[lip-6:lip+4,cx-reach:cx+reach+1].mean(axis=1)
    for k in (4, 5):
        if min(rows[k-2:k].mean(), rows[k+1:k+3].mean()) > .90 and rows[k] < .60:
            raise ValueError("预装颈口在沙柱接通后出现横贯出口的亮线")


def closing_surface(mask, cx, lip, half):
    """Measure the exposed last layer across the opening, including both edges."""
    low = max(0, int(lip - 1.2*half))
    heights = []
    for x in range(round(cx-.91*half), round(cx+.91*half)+1):
        rows = np.flatnonzero(np.convolve(mask[low:, x], np.ones(4), mode="valid") == 4)
        if not len(rows):
            raise ValueError("末段颈口缺少连续的沙面")
        heights.append(low + rows[0])
    span = np.percentile(heights, 95) - np.percentile(heights, 5)
    if span > max(3., .055*half):
        raise ValueError(f"归零前沙面在颈口有 {span:.1f} 像素的高低台阶")


def shoulder_gap(mask, settled, cx, lip, half, radius):
    """Find a notch between the bed and a shoulder, excluding open air above the cap."""
    reach = min(.84*radius, 1.8*half)
    left, right = max(0, round(cx-reach)), min(mask.shape[1], round(cx+reach)+1)
    widths = []
    for y in range(lip+1, min(mask.shape[0], lip+max(3, round(.23*radius)))):
        row = mask[y, left:right].astype(int)
        packed = np.flatnonzero(np.convolve(row, np.ones(3), mode="valid") == 3) + left
        width = 0
        for i in np.flatnonzero(np.diff(packed) > 3):
            start, stop = packed[i]+3, packed[i+1]
            offset = abs((start + stop) * .5 - cx)
            if .79*half <= offset <= 1.5*half and settled[y, start:stop].all():
                width = max(width, stop-start)
        widths.append(width)
    worst = np.percentile(widths, 95)
    if worst > max(3., .11*half):
        raise ValueError(f"归零前杯肩与沙层之间仍有 {worst:.1f} 像素的空槽")


def exposed_shoulder_drop(mask, cx, lip, radius):
    """Follow the bed on both sides before the last short column clears the neck."""
    def height(x):
        x = round(x)
        # Ignore sparse splash pixels above the connected bed.
        rows = mask[lip:lip+round(.85*radius), x-2:x+3].mean(axis=1)
        solid = np.flatnonzero(np.convolve(rows, np.full(5, .2), mode="valid") >= .80)
        if not len(solid):
            raise ValueError("末段堆肩没有可测的连续沙面")
        return solid[0]

    return min(height(cx+side*.61*radius) - height(cx+side*.53*radius) for side in (-1, 1))


def leading_fringe(rgb, geometry):
    """Measure from the first grain to the dense body separately in each central column."""
    cx, lip = round(geometry["cx"]), round(geometry["outlet_y"])
    half = max(3, round(.45*geometry["half_width"]))
    strip = rgb[lip-3:, cx-half-1:cx+half+2].astype(float)
    bg = geometry["background_rgb"]
    pigment = strip[:,:,0] - strip[:,:,2] - (bg[0]-bg[2])
    packed = np.median(pigment[:3,1:-1])
    first = pigment[3:-1,1:-1] > .40*packed
    # Average a 3 x 3 patch for the dense boundary; both arrays start at the lip.
    rows = sum(pigment[2+i:2+i+len(first)] for i in range(3))
    dense = (rows[:,:-2]+rows[:,1:-1]+rows[:,2:]) > (9.*.86*packed)
    if packed <= 0 or not (first.any(axis=0) & dense.any(axis=0)).all():
        raise ValueError("无法在首落前缘辨认颗粒和后方的连续沙柱")
    return float(np.median(dense[::-1].argmax(axis=0) - first[::-1].argmax(axis=0)))


def leading_opacity(rgb, geometry):
    """Smooth grain-scale variation while keeping holes within the advancing head."""
    cx, half = geometry["cx"], geometry["half_width"]
    lip, floor = round(geometry["outlet_y"]), round(geometry["surface_y"])
    xs = np.arange(round(cx-.70*half), round(cx+.70*half)+1)
    strip = rgb[lip-3:floor, xs].astype(np.int16)
    background = geometry["background_rgb"]
    pigment = strip[:,:,0] - strip[:,:,2] - (background[0]-background[2])
    packed = np.median(pigment[:3, abs(xs-cx) < .3*half])
    if packed <= 0:
        raise ValueError("无法识别首批沙粒的颜色")
    opacity = np.uint8(np.clip(pigment[3:] * (255./packed), 0, 255))
    smooth = Image.fromarray(opacity).filter(ImageFilter.GaussianBlur(max(.85, .045*half)))
    return xs, np.asarray(smooth)


def symmetric_nose(lanes, opacity, half, radius):
    """Are both the fringe and the dense edge close to one centred arc each?"""
    basis = np.column_stack((np.ones_like(lanes), lanes**2))
    for threshold in (.15, .45):
        covered = opacity >= 255.*threshold
        if not covered.any(axis=0).all():
            return False
        edge = len(covered)-1-covered[::-1].argmax(axis=0)
        coeff = np.linalg.lstsq(basis, edge, rcond=None)[0]
        bow = -coeff[1]*max(abs(lanes))**2
        residual = np.sqrt(np.mean((edge-basis@coeff)**2))
        # Resolve a visible bowed cap, allowing a reference pixel of edge
        # uncertainty. One exceptional grain cannot disguise a smooth bulk.
        if bow < .20*half or bow <= 10.*max(radius/209., residual):
            return False
    return True


def flat_nose(lanes, opacity, pitch):
    """Is the bulk edge level across the centre and both shoulders?"""
    core = abs(lanes) <= .65
    covered = opacity[:, core] > .58 * 255
    if not covered.size or not covered.any(axis=0).all():
        return False
    edge = covered[::-1].argmax(axis=0)
    # Two grain spacings rounded in native pixels allow a rough grain-scale
    # edge. Percentiles stop a stray grain from hiding a level cut.
    return np.ptp(np.percentile(edge, (10, 90))) <= max(1, round(2 * pitch))


def check_flat_opening(samples):
    """Reject a level cut that persists as long as a symmetric round nose."""
    since = None
    for t, flat in samples:
        if not flat:
            since = None
        elif since is None:
            since = t
        elif t - since >= .15 - 1.e-8:
            raise ValueError(f"{since:.3f}–{t:.3f} 秒首落前缘横贯两肩，持续呈平切")


def leading_profile(rgb, geometry):
    """Trace half-density sand across the moving nose, ignoring isolated grains."""
    cx, half, floor = geometry["cx"], geometry["half_width"], round(geometry["surface_y"])
    xs, opacity = leading_opacity(rgb, geometry)
    dense = opacity > .58*255
    if not dense.any(axis=0).all():
        raise ValueError("无法在首落前缘的各横向位置追踪沙柱")
    heights = floor-1 - dense[::-1].argmax(axis=0)
    return [np.median(heights[abs(xs-(cx+u*half)) < max(1.1, .05*half)])
            for u in np.linspace(-.60, .60, 13)]


def head_area_samples(mask, geometry):
    """Follow the bulk leading edge. Isolated outriders cannot move the window."""
    half = geometry["half_width"]
    cx, lip, floor = (round(geometry[k]) for k in ("cx", "outlet_y", "surface_y"))
    span = round(2.8 * half)
    strip = mask[lip:floor, cx-span:cx+span+1]
    core = abs(np.arange(-span, span+1)) < .65 * half
    coverage = strip[:, core].mean(axis=1)
    leading = np.flatnonzero(np.convolve(coverage, np.ones(5)/5, "same") >= .30)
    if not len(leading):
        return None
    front = leading[-1]
    if not .40 < front / (floor-lip) < .90:
        return None
    widths, filled = [], []
    for lo, hi in ((0., .4), (.4, .8), (.8, 1.2), (2.7, 3.2)):
        band = strip[front-round(hi*half):front-round(lo*half)+1]
        xs = np.nonzero(band)[1]
        if len(xs) < 15:
            raise ValueError("无法辨认首落头部的连续沙粒")
        widths.append(np.ptp(np.percentile(xs, (5, 95))))
        filled.append([cell.mean() for cell in np.array_split(band[:, core], 5, axis=1)])
    return (front / (floor-lip), np.asarray(widths[:3]) / max(1., widths[3]),
            np.asarray(filled[:3]))


def check_head_area(samples):
    for lo, hi in ((.40, .66), (.66, .90)):
        window = [(widths, filled) for depth, widths, filled in samples if lo < depth <= hi]
        if len(window) < 3:
            raise ValueError("缺少 50 秒档半程和触底前的连续头部画面")
        # Require visible sand and visible gaps in most cells of every row.
        passed = [np.all((((f > .08) & (f < .85)).sum(axis=1)) >= 3)
                  for _, f in window]
        if np.mean(passed) < .70:
            raise ValueError(f"50 秒档头部大部分区域未散开，落距 {lo:.0%}–{hi:.0%} 仅 {np.mean(passed):.0%} 的帧满足")
        flare = np.percentile([np.median(w) for w, _ in window], 90)
        # Use the same visible bulge tolerance as for the 5 s and 15 s heads.
        # Longitudinal gaps need no minimum transverse expansion.
        if flare > 1.12:
            raise ValueError(f"50 秒档头部持续向外张开，宽度达到后方沙柱的 {flare:.2f} 倍")


def head_transition_bands(mask, cx, lip, half, floor):
    """Sample adjacent half-mouth-radius strips behind the leading grains."""
    span = round(half)
    strip = mask[lip:floor, cx-span:cx+span+1]
    occupied = np.flatnonzero(strip.sum(axis=1) >= 3)
    if not len(occupied) or not .32 < occupied[-1] / (floor-lip) < .58:
        return None
    x = np.arange(-span, span+1)/half
    depth = (occupied[-1]-np.arange(len(strip)))/half
    regions = [abs(x) < .30, (-.80 <= x) & (x <= -.55), (.55 <= x) & (x <= .80)]
    return np.array([[1.-strip[(lo <= depth) & (depth < lo+.5)][:, r].mean()
                      for lo in np.arange(0., 3., .5)] for r in regions])


def check_head_transition(samples, sample_rate):
    # At 60 Hz this covers three consecutive frames of the original film.
    window = max(2, int(np.ceil(.05*sample_rate)))
    if len(samples) < window:
        raise ValueError("缺少首落头部后半段的连续画面")
    holes = np.asarray(samples)
    if np.median(holes[:, 0, 3]) < .08:
        raise ValueError("首落头部后半段的中央过早变实")
    reversal = np.diff(holes[:, 1:, 2:], axis=2)
    for side, name in enumerate(("左", "右")):
        for band in reversal[:, side, :].T:
            excess = np.convolve(band, np.full(window, 1./window), 'valid').max()
            if excess > .065:
                raise ValueError(f"首落头部后方的{name}缘持续由密返稀，露底倒差 {excess:.1%}")


def opening_seam_contrast(mask, cx, lip, half):
    """Excess glass visible in a horizontal band between two dense layers."""
    reach = max(1, round(.72*half))
    step = max(1, round(.35*half))
    smooth = 2*max(1, round(.10*half)) + 1
    strip = mask[lip:lip+round(3.5*half)+smooth, cx-reach:cx+reach+1]
    holes = np.convolve(1. - strip.mean(axis=1), np.ones(smooth)/smooth, "same")
    rows = np.arange(max(step, round(.48*half)), min(len(holes)-step, round(2.85*half)))
    neighbours = np.maximum(holes[rows-step], holes[rows+step])
    return float(np.max(np.where(neighbours < .035, holes[rows]-neighbours, 0.), initial=0.))


def opening_edge_reversal(mask, cx, lip, half, tip):
    """Largest local increase in sand coverage going down either shoulder."""
    width = max(1, round(.73*half))
    rows = np.arange(round(1.2*half), min(round(.59*(tip-lip)), round(3.5*half)))
    if not rows.size:
        raise ValueError("首落边缘未覆盖两段相邻的沙流")
    x = (np.arange(mask.shape[1]) + .5-cx) / half
    differences = []
    for side in (-x, x):
        holes = 1. - mask[lip:rows[-1]+lip+2*width, (.60 < side) & (side < .81)].mean(axis=1)
        bands = np.convolve(holes, np.ones(width)/width, "valid")
        differences.append(np.max(bands[rows]-bands[rows+width]))
    return max(differences)


def landing_bulk_gap(folder, frames, contact, sample_rate):
    """Compare the visible half-density front with the receiving surface at first landing."""
    window = [f for f in frames if 1/30-1e-9 <= f["time"]-contact <= .10+1e-9]
    if not window or frames[-1]["time"] < contact+.10-1/sample_rate-1e-9:
        raise ValueError("缺少首次接堆后的连续画面")
    gaps = []
    for frame in window:
        geo = frame["geometry"]
        cx, lip, bed = (round(geo[k]) for k in ("cx", "outlet_y", "surface_y"))
        half = geo["half_width"]
        reach = max(1, int(.5*half))
        # Exclude the heap itself and measure the centre of each five-row window.
        mask = gold(frame_image(folder, frame))
        coverage = mask[lip:bed-3, cx-reach:cx+reach+1].mean(axis=1)
        dense = np.flatnonzero(np.convolve(coverage, np.ones(5)/5, "valid") >= .50)
        gap = bed - (lip + dense[-1] + 2) if dense.size else bed-lip
        if gap > .63*half:
            raise ValueError(f"{frame['time']:.3f} 秒首堆已出现，沙柱主体距堆顶仍有 {gap} 像素")
        gaps.append(int(gap))
    return max(gaps)


def central_head_transition(rgb, geometry):
    """Depth of the visible breakup inside the nose, excluding isolated leaders."""
    xs, opacity = leading_opacity(rgb, geometry)
    center = abs(xs - geometry["cx"]) < .50 * geometry["half_width"]
    coverage = opacity[:, center].mean(axis=1)
    porous = np.flatnonzero(coverage > .35 * 255)
    dense = np.flatnonzero(coverage > .85 * 255)
    if not len(porous) or not len(dense):
        raise ValueError("首落中央缺少可辨认的密集层或疏松层")
    return int(porous[-1] - dense[-1])


def airborne_head_section(rgb, geometry):
    """Width of the porous layer and centre-to-shoulder bow, in original pixels."""
    xs, opacity = leading_opacity(rgb, geometry)
    x = (xs-geometry["cx"]) / geometry["half_width"]
    inner = abs(x) < .60
    # Work up from open air. One detached grain should not stand in for a
    # porous layer across the nose. Measure both boundaries in the same lane.
    def from_below(level):
        mask = opacity[::-1] > level*255
        if not mask[:, inner].any(axis=0).all():
            raise ValueError("首落头部缺少可辨认的颗粒层次")
        return mask.argmax(axis=0)

    loose, middle, packed = (from_below(level) for level in (.18, .58, .86))
    layer = np.percentile((packed-loose)[inner], 25)
    shoulders = (np.median(middle[(-.60 < x) & (x < -.42)]) +
                 np.median(middle[(.42 < x) & (x < .60)])) / 2.
    return layer, shoulders - np.median(middle[abs(x) < .12])


def head_width_ratio(rgb, geometry, tip):
    """Compare the rear of the moving head to the following free stream."""
    cx, half = geometry["cx"], geometry["half_width"]
    lip = round(geometry["outlet_y"])
    reach = round(1.25*half)
    strip = rgb[lip-3:tip+1, round(cx)-reach:round(cx)+reach+1].astype(np.int16)
    bg = geometry["background_rgb"]
    pigment = strip[:,:,0] - strip[:,:,2] - (bg[0]-bg[2])
    packed = np.median(pigment[:3, reach-2:reach+3])
    if packed <= 0:
        raise ValueError("首落头部宽度缺少颈口沙色参照")
    widths = (pigment[3:] > .48*packed).sum(axis=1)
    distance = tip-lip-np.arange(len(widths))
    head = widths[(.90*half <= distance) & (distance <= 1.70*half)]
    body = widths[(2.2*half <= distance) & (distance <= 3.1*half)]
    if not len(body) or np.median(body) < 1:
        raise ValueError("首落头部后方缺少可比较的沙流")
    return float(np.percentile(head, 75) / np.median(body))


def head_shoulder_density(mask, geometry):
    """Measure both shoulders up from the first few visible grains in a free jet."""
    half, cx = geometry["half_width"], geometry["cx"]
    lip, floor = round(geometry["outlet_y"]), round(geometry["surface_y"])
    span = round(1.1*half)
    center = round(cx)
    strip = mask[lip:floor, center-span:center+span+1]
    occupied = np.flatnonzero(strip.sum(axis=1) >= 3)
    if not len(occupied):
        return None
    depth = occupied[-1]
    if not 2.7*half <= depth <= .93*(floor-lip):
        return None
    x = (np.arange(center-span, center+span+1) + .5-cx) / half
    # Start above the outermost dust. It can hide a dense band just behind it.
    behind = (depth-np.arange(depth+1)) / half
    bands = [(.42, .94), (.99, 1.53), (1.60, 2.20)]
    coverage = []
    sections = [(.55 <= side) & (side < .94) for side in (-x, x)]
    for section in [*sections, abs(x) < .38]:
        rows = strip[:depth+1, section].mean(axis=1)
        coverage.append([rows[(lo < behind) & (behind <= hi)].mean() for lo, hi in bands])
    return depth, np.array(coverage)


def check_head_release_order(samples, half):
    if len(samples) < 3:
        raise ValueError("缺少首落头部和后方两侧的连续画面")
    depths, bands = zip(*samples)
    bands = np.asarray(bands)
    # A light patch on either shoulder followed by a denser head is visible
    # even if the average over the full jet or the full opening looks right.
    shoulders = bands[:,:2]
    difference = shoulders[:,:,0] - shoulders[:,:,1:].min(axis=2)
    for side in difference.T:
        start = None
        for depth, excess in zip(depths, side):
            if excess <= .065:
                start = None
            elif start is None:
                start = depth
            elif depth-start >= .32*half:
                raise ValueError("首落头部下方重新变密，上方两侧先露出空隙")
    median = float(np.median(shoulders[:,:,1] - shoulders[:,:,0]))
    if median < .025:
        raise ValueError("首落头部下方未先松开，与后方相比缺少可见的疏密过渡")
    # Squeezing the lower outline would also expose the background at its
    # sides. Verify that the inner grains have actually separated early on.
    early = bands[np.asarray(depths) <= 4.3*half, 2]
    if not len(early) or np.median(early[:,1] - early[:,0]) < .12:
        raise ValueError("首落初段只收窄了边缘，前端内部仍挤在一起")
    return round(100*median, 1)


def check_airborne_head(sections, half):
    if len(sections) < 3:
        raise ValueError("缺少首次触地前的连续头部画面")
    layer, bow = np.median(sections, axis=0)
    if layer < .33*half or bow > .24*half:
        raise ValueError(f"首落头部持续像一个实心圆帽，颗粒过渡 {layer:.1f} 像素，中央下凸 {bow:.1f} 像素")
    if layer > .95*half:
        raise ValueError(f"首落头部散成雾，颗粒过渡长达 {layer:.1f} 像素")
    return dict(porous_layer_px=round(float(layer), 2), bow_px=round(float(bow), 2))


def leading_deformation(tips, profiles, radius):
    """Compare local changes after removing translation, tilt and overall rounding."""
    tips, shapes = np.asarray(tips), np.asarray(profiles)
    if len(shapes) < 3:
        raise ValueError("首落前缘轮廓样本不足")
    x = np.linspace(-1., 1., shapes.shape[1])
    basis = np.column_stack((np.ones_like(x), x, x*x))
    residual = shapes - (basis @ np.linalg.lstsq(basis, shapes.T, rcond=None)[0]).T

    def change(distance):
        values = []
        for i, tip in enumerate(tips):
            ahead = np.flatnonzero(tips[i+1:]-tip >= distance)
            if ahead.size:
                difference = residual[i+1+ahead[0]]-residual[i]
                values.append(np.sqrt(np.mean(difference**2)))
        if len(values) < 3:
            raise ValueError("首落前缘的连续帧没有覆盖足够的下落距离")
        return float(np.median(values))

    local, far = change(.07*radius), change(.35*radius)
    if far < .012*radius:
        raise ValueError(f"首落头部近似整块平移，扣除整体运动后局部轮廓只变 {far:.2f} 像素")
    if far < 1.6*local:
        raise ValueError(f"前缘的形变集中在短间隔，近距与远距分别为 {local:.2f}、{far:.2f} 像素")
    return round(far, 2)


def persistent_leading_spike(tips, profiles, half, radius):
    """How far a narrow protrusion travels while leading both of its shoulders."""
    shape = np.asarray(profiles)
    if len(shape) < 3:
        raise ValueError("首落前缘轮廓样本不足")
    prominence = shape[:,3:-3] - np.maximum(shape[:,:-6], shape[:,6:])
    raised = prominence > .26*half
    # Allow the same tuft to drift one sample sideways as it falls.
    nearby = raised[:,:-1] | raised[:,1:]
    start = np.full(nearby.shape[1], np.nan)
    longest = 0.
    for tip, active in zip(tips, nearby):
        start = np.where(active, np.where(np.isnan(start), tip, start), np.nan)
        if active.any():
            longest = max(longest, float(tip-np.min(start[active])))
    if longest > .26*radius:
        raise ValueError(f"首落有一束沙持续成尖，下落 {longest:.1f} 像素后仍领先两侧")
    return round(longest, 2)


def leading_motion(images, tips, cx, half, radius):
    """Track the same small near-tip patch when the visible front advances."""
    ratios = []
    width = max(2, round(min(.17*half, .03*radius)))
    height = max(3, round(.023*radius))
    for i, (first, start) in enumerate(zip(images, tips)):
        j = next((k for k in range(i+1, len(tips)) if tips[k]-start >= .14*radius), None)
        if j is None:
            continue
        travel = tips[j]-start
        row = start - height - max(3, round(.13*half))
        cols = slice(cx-width, cx+width+1)
        template = first[row-height:row+height+1, cols].astype(float)
        template -= template.mean()
        norm = np.linalg.norm(template)
        if norm < 1.e-6:
            continue
        scores = []
        for shift in range(round(travel*1.35)+2):
            patch = images[j][row-height+shift:row+height+1+shift, cols].astype(float)
            patch -= patch.mean()
            scores.append((template*patch).sum() / max(1.e-8, norm*np.linalg.norm(patch)))
        best = int(np.argmax(scores))
        if scores[best] >= .4:
            ratios.append(best/travel)
    if len(ratios) < 2 or not .70 <= np.median(ratios) <= 1.30:
        raise ValueError(f"首落前端和紧随纹理的位移不同步，可信样本 {np.round(ratios,2)}")
    return round(float(np.median(ratios)), 3)


def motion_speed(a, b, dt, cx, row, half, radius):
    """Downward displacement from the best local normalized brightness match."""
    width = max(1, round(.30 * half))
    height = max(3, round(.031 * radius))
    x = slice(cx - width, cx + width + 1)
    template = a[row-height:row+height+1, x].astype(float)
    template -= template.mean()
    norm = np.linalg.norm(template)
    if norm < 1e-6:
        return None
    scores = []
    for dy in range(int(np.ceil(dt * .92 * radius)) + 2):
        moved = b[row-height+dy:row+height+dy+1, x].astype(float)
        moved -= moved.mean()
        scores.append(float((template*moved).sum() / max(1e-9, norm*np.linalg.norm(moved))))
    best = int(np.argmax(scores))
    if not 0 < best < len(scores)-1 or scores[best] < .40:
        return None
    left, peak, right = scores[best-1:best+2]
    shift = .5 * (left-right) / (left+right-2*peak) if left+right != 2*peak else 0.
    return (best + shift) / dt


def closing_core_ratio(rgb, geometry):
    """Compare pigment above the pile while the last packed layer still feeds the lip."""
    cx, lip = round(geometry["cx"]), round(geometry["outlet_y"])
    gap = geometry["surface_y"] - lip
    half = round(.55 * geometry["half_width"])
    if gap < 18 or half < 5:
        return None
    core = rgb[:, cx-half:cx+half+1].astype(float)
    background = geometry["background_rgb"]
    pigment = core[:,:,0] - core[:,:,2] - (background[0]-background[2])
    upper = pigment[lip-3:lip]
    if np.percentile(upper, 10) < 80:
        return None  # The trailing surface has already entered this strip.
    middle = pigment[lip+round(.14*gap):lip+round(.41*gap)].mean()
    lower = pigment[lip+round(.55*gap):lip+round(.74*gap)].mean()
    return float(middle / min(upper.mean(), lower)) if lower > 0 else None


def closing_step_ratio(samples, end):
    """Compare the fastest post-cutoff loss of sand colour with the incoming surface."""
    before, after = [], []
    for (t0, a), (t1, b) in zip(samples, samples[1:]):
        speed = (a-b) / (t1-t0)
        if end-.068 <= t0 and t1 <= end+1.e-8:
            before.append(speed)
        elif t1 > end+1.e-8 and t0 < end+.05-1.e-8:
            after.append(speed)
    if not before or not after or np.median(before) <= 0.:
        raise ValueError("缺少跨过归零时刻的连续末层画面")
    ratio = float(max(after) / np.median(before))
    if ratio > 1.8:
        raise ValueError(f"归零相邻帧沙柱退去速度突然变为先前的 {ratio:.2f} 倍")
    return ratio


def splash_pixels(mask, lip, cx, half, radius):
    """Exposed gold above each coherent slope, away from the dense incoming jet."""
    count = 0
    for x in range(max(0, int(cx-.81*radius)), min(mask.shape[1], int(cx+.81*radius))):
        if abs(x-cx) <= .92 * half:
            continue
        column = mask[lip+3:, x]
        run = max(4, round(.027*radius))
        solid = np.flatnonzero(np.convolve(column, np.ones(run), mode="valid") >= run)
        if len(solid):
            bed = solid[0]
            count += column[max(0,bed-round(.16*radius)):max(0,bed-max(2,round(.014*radius)))].sum()
    return int(count)


def first_deposit(frames, contact, period, sample_rate):
    """Compare the floor pixels with the first credited mass and the rise of the bed."""
    if not math.isfinite(sample_rate) or sample_rate <= 0 or any(
            not math.isclose(f["time"], i / sample_rate, abs_tol=1e-7, rel_tol=0.)
            for i, f in enumerate(frames)):
        raise ValueError("首堆复查需要从开场按采样率连续记录的帧")
    first = next((i for i, f in enumerate(frames) if f["transfer"]["landed"] > 1e-12), None)
    if first is None or abs(frames[first]["time"] - contact) > 1. / sample_rate + 1e-8:
        raise ValueError("杯底首次见沙与首批沙计入下杯相差超过一帧")
    if period > 5.:
        return
    # A jump can also occur on the frame after a token first deposit. Compare
    # complete 1/60 s intervals through the first 0.08 s, at native glass scale.
    hop = max(1, round(sample_rate / 60.))
    if first == 0 or frames[-1]["time"] < contact + .08 - 1e-8:
        raise ValueError("缺少首批着地前后连续的堆面")
    mouth = 2. * frames[0]["geometry"]["half_width"]
    for i in range(first, len(frames)):
        if frames[i]["time"] > contact + .08 + 1e-8:
            break
        a, b = frames[max(0, i-hop)], frames[i]
        dt = b["time"] - a["time"]
        rise = a["geometry"]["surface_y"] - b["geometry"]["surface_y"]
        if rise > max(2., .20 * mouth) * max(1., 60.*dt) + 1e-6:
            raise ValueError(f"{b['time']:.3f} 秒首堆突然升高 {rise:.1f} 像素，口径 {mouth:.1f} 像素")


def opening_edges(mask, cx, lip, floor, half):
    """Depths of the dense material and the visible fringe in the same frame."""
    reach = max(1, round(.48 * half))
    coverage = mask[lip:floor, cx-reach:cx+reach].mean(axis=1)
    height = max(1, round(.16 * half))
    bands = np.convolve(coverage, np.full(height, 1. / height), mode="valid")
    edges = []
    for level in (.90, .12):
        rows = np.flatnonzero(bands >= level)
        edges.append(float(rows[-1] + (height + 1)/2) if rows.size else 0.)
    return edges


def early_packed_coverage(mask, cx, lip, floor, half, pitch):
    """Measure the next few grain layers, excluding the outermost grains."""
    reach = max(1, round(.48*half))
    strip = mask[lip:floor, cx-reach:cx+reach]
    rows = np.flatnonzero(strip.sum(axis=1) >= max(2, round(.10*strip.shape[1])))
    tip = int(rows[-1])+1 if rows.size else 0
    if not (.65*half <= tip <= 1.25*half and tip > 9*pitch):
        return None
    near = max(1, round(3*pitch))
    far = max(near+1, round(9*pitch))
    return float(strip[tip-far:tip-near].mean())


def check_opening_braking(samples, radius):
    """Compare adjacent 100 ms windows throughout the free fall on the recorded clock."""
    points = np.asarray(samples, dtype=float)
    if len(points) < 6 or not np.isfinite(points).all():
        raise ValueError("首落连续画面不足，无法复核密实沙团的运动")
    times, body, fringe = points.T
    gaps = np.diff(times)
    if np.any(gaps <= 0.) or np.any(gaps > .05 + 1e-8):
        raise ValueError("首落时间戳不连续或取样不足，每 0.10 秒至少需要两个采样间隔")
    tolerance = 2. * radius / 209.
    previous_failure = None
    observed = False
    for index, end in enumerate(times):
        if end - .20 < times[0] - 1e-8:
            continue
        start, middle = end - .20, end - .10
        if np.interp(start, times, body) <= 2*tolerance:
            continue
        observed = True
        before, after = [], []
        for edge in (body, fringe):
            pivot = np.interp(middle, times, edge)
            before.append(pivot - np.interp(start, times, edge))
            after.append(edge[index] - pivot)
        before, after = np.array(before), np.array(after)
        braking = np.all(after <= .70 * before) and np.all(before-after > tolerance)
        hanging = after[0] <= tolerance and (after[1] <= tolerance or after[1] > 2*tolerance
                                              or before[0] > 2*tolerance)
        if (braking or hanging) and previous_failure == index-1:
            raise ValueError(
                f"首落沙团停滞或明显减速，{start:.3f}–{middle:.3f} 与 {middle:.3f}–{end:.3f} 秒："
                f"密实下缘下移 {before[0]:.1f}、{after[0]:.1f} 像素，"
                f"散粒前缘下移 {before[1]:.1f}、{after[1]:.1f} 像素")
        previous_failure = index if braking or hanging else None
    if not observed:
        raise ValueError("缺少密实沙团出颈口后的连续画面")


def review(folder, opening_only=False):
    meta = json.loads((folder / "motion.json").read_text(encoding="utf-8"))
    frames = meta["frames"]
    if (not meta["completed"] or meta.get("crop_box") or meta["color"] != 0 or
            len(frames) != meta["expected_frames"] or frames[0]["time"] != 0 or
            not all(f["native_hidden"] and "transfer" in f for f in frames)):
        raise ValueError("需要从开始连续取样的隐藏 PC 全幅金沙原图和逐帧沙量")
    geo = frames[0]["geometry"]
    radius, half = geo["radius"], geo["half_width"]
    cx, lip, floor = [round(geo[key]) for key in ("cx","outlet_y","surface_y")]
    period = meta["period"]
    if not opening_only and (frames[-1]["time"] < period + .25
                             or frames[-1]["transfer"]["landed"] < 1. - 1e-8):
        raise ValueError("完整复查须覆盖归零、在途颗粒落定及归零后至少四分之一秒")
    settled = None
    if frames[-1]["transfer"]["landed"] >= 1. - 1e-8:
        final_image = frame_image(folder, frames[-1])
        sphere_endpoint(final_image, geo, upper=False)
        settled = gold(final_image)
    initial_image = frame_image(folder, frames[0])
    gap = primed_reservoir(initial_image, geo)

    contact = None
    opening_motion = []
    flat_opening = []
    open_layers_since = None
    symmetric_since = None
    symmetric_frames = 0
    above, times, masks, luminance, spray, closing_ratios = [], [], [], [], [], []
    tail_fringe, cutoff_samples = [], []
    head_frames, head_tips, front_fringe, shoulders = [], [], [], []
    central_transitions = []
    emergence = []
    head_areas = []
    head_bands = []
    seam_contrasts = []
    edge_reversals = []
    profile_tips, profiles, airborne_sections, head_widths, shoulder_samples = [], [], [], [], []
    for frame in frames:
        t = frame["time"]
        if t >= period + .25 and frame["transfer"]["landed"] >= 1. - 1e-8:
            sphere_endpoint(frame_image(folder, frame), geo, upper=False)
            continue
        layer = (1. - t/period) * (4.*radius**3/3.) / half**3
        closing = 0. < layer < .65
        released_tail = period == 15 and 0. < t-period <= .05 and frame["transfer"]["flight"] > 1e-9
        cutoff = period in (5,15) and abs(t-period) < .12
        near_full = period == 15 and .970 <= frame["transfer"]["landed"] <= .978
        if contact is None or closing or cutoff or near_full or (.24*period < t < .60*period) or (3 <= t <= 4.5):
            rgb = frame_image(folder, frame)
            mask = gold(rgb)
            if 0. < t <= min(.5, .5*period):
                check_filled_lip_seam(mask, geo)
        else:
            continue
        if cutoff:
            width = round(.78*half)
            strip = rgb[lip-round(.95*half):lip+round(1.3*half), cx-width:cx+width+1].astype(float)
            bg = geo["background_rgb"]
            pigment = strip[:,:,0] - strip[:,:,2] - (bg[0]-bg[2])
            cutoff_samples.append((t, pigment.mean()))
        if contact is None:
            bottom = mask[floor-max(2,round(.009*radius)):floor+1, cx-round(half):cx+round(half)+1]
            if bottom.any():
                contact = t
            elif period in (1,5,15,50):
                if period in (5,15):
                    shoulder = head_shoulder_density(mask, geo)
                    if shoulder is not None:
                        shoulder_samples.append(shoulder)
                central = mask[lip:floor, cx-2:cx+3]
                tip = floor-1 - int(np.median(np.argmax(central[::-1], axis=0)))
                if period == 50:
                    repeats_arc = False
                    flat = False
                    if central.any(axis=0).all() and lip + .30*half <= tip < floor:
                        xs, opacity = leading_opacity(rgb, geo)
                        lanes = (xs-geo["cx"])/half
                        repeats_arc = symmetric_nose(lanes, opacity, half, radius)
                        flat = flat_nose(lanes, opacity, frame["draw_state"]["pitch"])
                    flat_opening.append((t, flat))
                    if repeats_arc:
                        if symmetric_since is None:
                            symmetric_since = t
                        symmetric_frames += 1
                        if symmetric_frames >= 3 and t-symmetric_since >= .15-1.e-8:
                            raise ValueError(f"{symmetric_since:.3f}–{t:.3f} 秒首落的散粒外沿和主体下缘持续呈居中对称圆头")
                    else:
                        symmetric_since = None
                        symmetric_frames = 0
                    coverage = early_packed_coverage(mask, cx, lip, floor, half, frame["draw_state"]["pitch"])
                    if coverage is not None and coverage < 2./3.:
                        if open_layers_since is None:
                            open_layers_since = t
                        elif t-open_layers_since >= .025-1e-8:
                            raise ValueError(f"{t:.3f} 秒刚出颈口的前几层已出现大片空隙，沙色覆盖 {coverage:.1%}")
                    else:
                        open_layers_since = None
                    opening_motion.append((t, *opening_edges(mask, cx, lip, floor, half)))
                    if len(emergence) < 5:
                        rows = np.flatnonzero(mask[lip:floor, cx-3:cx+4].sum(axis=1) >= 2)
                        if rows.size or emergence:
                            emergence.append(int(rows[-1])+1 if rows.size else 0)
                        if len(emergence) == 5:
                            step = max(1., float(np.median(np.diff(emergence))))
                            if emergence[0] > 2*step + 2*radius/209.:
                                raise ValueError(f"首帧突然伸出 {emergence[0]} 像素，随后每帧约 {step:.1f} 像素")
                    if .80-1e-9 <= t <= 1.+1e-9:
                        seam_contrasts.append(opening_seam_contrast(mask, cx, lip, half))
                    if 1.-1e-9 <= t <= 1.17:
                        edge_reversals.append(opening_edge_reversal(mask, cx, lip, half, tip))
                    area = head_area_samples(mask, geo)
                    if area is not None:
                        head_areas.append(area)
                    bands = head_transition_bands(mask, cx, lip, half, floor)
                    if bands is not None:
                        head_bands.append(bands)
                if period == 50 and .60 < (tip-lip)/(floor-lip) < .95:
                    central_transitions.append(central_head_transition(rgb, geo))
                if period != 50 and lip+.18*(floor-lip) < tip < lip+.85*(floor-lip):
                    head_frames.append(rgb[:,:,1])
                    head_tips.append(tip)
                    if period in (5,15) and .36 < (tip-lip)/(floor-lip) < .75:
                        front_fringe.append(leading_fringe(rgb, geo))
                if period in (5,15) and lip+.26*(floor-lip) < tip < lip+.96*(floor-lip):
                    profile_tips.append(tip)
                    profiles.append(leading_profile(rgb, geo))
                    if lip+.45*(floor-lip) < tip < lip+.93*(floor-lip):
                        airborne_sections.append(airborne_head_section(rgb, geo))
                        head_widths.append(head_width_ratio(rgb, geo, tip))
        if near_full:
            shoulders.append(exposed_shoulder_drop(mask, cx, lip, radius))
        if closing:
            closing_surface(mask, cx, lip, half)
            ratio = closing_core_ratio(rgb, frame["geometry"])
            if ratio is not None:
                closing_ratios.append(ratio)
                if ratio < .94:
                    raise ValueError(f"{t:.3f} 秒仍在供沙，空中的沙柱出现横向浅带，柱心相对浓度 {ratio:.3f}")
            if layer <= .30 and settled is not None:
                shoulder_gap(mask, settled, cx, lip, half, radius)
        if released_tail:
            width = round(.44*half)
            core = mask[lip:, cx-width:cx+width+1].mean(axis=1)
            solid = np.flatnonzero(core >= .90)
            if solid.size:
                exposed = core[:solid[0]]
                tail_fringe.append(np.count_nonzero((exposed > .025) & (exposed < .85)))
        if period <= 15 and .24*period < t < .60*period:
            spray.append(splash_pixels(mask, lip, cx, half, radius))
        if period >= 5 and 3 <= t <= 4.5 and frame["geometry"]["surface_y"]-lip > 1.04*radius:
            luminance.append(rgb[:,:,1])
            times.append(t)
        if contact is not None and .24*period < t < .60*period:
            # Hold the vertical observation band fixed relative to the real glass.
            if frame["geometry"]["surface_y"] - lip > 2*half:
                h = max(2, round(.028*radius))
                masks.append(mask[lip+1:lip+h+1, cx-round(half)-2:cx+round(half)+3])
                above.append(mask[lip-3, cx-round(half)-2:cx+round(half)+3].sum())

    windows = {1:(.14,.26), 5:(.30,.64), 15:(.80,1.5), 20:(1.16,1.66),
               50:(1.20,1.65), 6000:(1.20,1.65)}
    if contact is None:
        raise ValueError("连续帧未覆盖首次可见触底")
    if period == 50:
        check_opening_braking(opening_motion, radius)
    first_deposit(frames, contact, period, meta["sample_rate"])
    if period in windows:
        low, high = windows[period]
        if not low <= contact <= high + 1/meta["sample_rate"]:
            raise ValueError(f"{period:g} 秒档首落 {contact:.3f} 秒")
    deformation = spike_travel = airborne_head = None
    if period == 50:
        check_flat_opening(flat_opening)
        if len(seam_contrasts) < 3:
            raise ValueError("缺少 50 秒档 0.8～1.0 秒出口下方的连续画面")
        seam = float(np.median(seam_contrasts))
        if seam > .012:
            raise ValueError(f"首批沙粒和后续沙流之间有持续浅带，露底比例高出两侧 {100*seam:.2f} 个百分点")
        if len(edge_reversals) < 3:
            raise ValueError("缺少 50 秒档 1.00～1.17 秒两侧边缘的连续画面")
        if np.median(edge_reversals) > .04:
            raise ValueError(f"沙柱中段边缘先松开，下方反而更密，相邻段露底倒差 {np.median(edge_reversals):.1%}")
        bulk_gap = landing_bulk_gap(folder, frames, contact, meta["sample_rate"])
        check_head_area(head_areas)
        check_head_transition(head_bands, meta["sample_rate"])
        if len(central_transitions) < 3:
            raise ValueError("缺少 50 秒档触底前的连续头部画面")
        transition = float(np.median(central_transitions))
        if transition < .70 * half:
            raise ValueError(f"50 秒档首落中央仍是密实圆头，过渡仅 {transition:.1f} 像素")
        airborne_head = dict(central_transition_px=round(transition, 1), landing_bulk_gap_px=bulk_gap,
                             opening_seam_pp=round(100*seam, 2))
    if period in (5,15):
        mid = np.asarray(profile_tips) < lip+.85*(floor-lip)
        deformation = leading_deformation(np.asarray(profile_tips)[mid], np.asarray(profiles)[mid], radius)
        spike_travel = persistent_leading_spike(profile_tips, profiles, half, radius)
        airborne_head = check_airborne_head(airborne_sections, half)
        airborne_head["shoulder_coverage_drop_pp"] = check_head_release_order(shoulder_samples, half)
        width = float(np.median(head_widths))
        if width > 1.12:
            raise ValueError(f"首批散粒后方形成鼓包，宽度是相邻沙柱的 {width:.2f} 倍")
        airborne_head["width_ratio"] = round(width, 3)
    if opening_only:
        return dict(period=period, crown_gap_px=gap, contact_seconds=contact, leading_deformation_px=deformation,
                    persistent_spike_travel_px=spike_travel, airborne_head=airborne_head)
    if period in (5,15) and (not front_fringe or np.median(front_fringe) < max(3., .09*half)):
        raise ValueError(f"首落前缘近似实心圆头，散粒到致密层的距离为 {front_fringe} 像素")
    if period == 15 and (not shoulders or np.median(shoulders) < max(1.5, .009*radius)):
        raise ValueError(f"临近装满时沙堆两侧出现水平肩部，外侧落差为 {shoulders} 像素")
    cutoff_ratio = closing_step_ratio(cutoff_samples, period) if period in (5,15) else None
    lead_ratio = leading_motion(head_frames, head_tips, cx, half, radius) if period in (1,5,15) else None
    width_ratio = None
    if masks:
        occupied = np.mean(masks, axis=0) >= .95
        width_ratio = float(np.median(occupied.sum(axis=1)) / max(1., np.median(above)))
        if width_ratio < .92:
            raise ValueError(f"出口后致密宽度仅为颈内的 {width_ratio:.1%}")
    if period <= 50 and not masks:
        raise ValueError("缺少稳定供沙时的出口像素，无法复核衔接")
    if period in (1,5,15) and (not spray or np.median(spray) < 2):
        raise ValueError("中段落点两侧持续看不到可分辨的飞溅")
    speed = []
    if period == 15:
        if not closing_ratios:
            raise ValueError("没有可比较的末段杯口、在途沙和堆顶上方三段纹理")
        if not tail_fringe or max(tail_fringe) < max(3, .022*radius):
            raise ValueError(f"停供后尾端仍然太整齐，可辨散粒的行数为 {tail_fringe}")
        hops = sorted({max(1, round(t * meta["sample_rate"])) for t in (.09, .125, .15)})
        for depth in (.045, .34, .635, .93):
            values = []
            for hop in hops:
                matches = []
                for i in range(len(times)-hop):
                    dt = times[i+hop] - times[i]
                    v = motion_speed(luminance[i], luminance[i+hop], dt, cx,
                                     lip+round(depth*radius), half, radius)
                    if v is not None:
                        matches.append(v)
                if len(matches) < 5:
                    raise ValueError("纹理追踪缺少连续、可信的位移")
                values.append(np.median(matches))
            speed.append(round(float(np.median(values)), 2))
        increments = np.diff(speed)
        if not (min(increments) >= .50*max(increments) > 0 and speed[1] > 1.17*speed[0]
                and 1.55 < speed[-1]/speed[0] < 1.85):
            raise ValueError(f"柱内四处纹理速度 {speed} 像素/秒")
    return dict(period=period, crown_gap_px=gap, contact_seconds=contact,
                tip_texture_travel_ratio=lead_ratio,
                leading_deformation_px=deformation, airborne_head=airborne_head,
                persistent_spike_travel_px=spike_travel,
                leading_fringe_px=float(np.median(front_fringe)) if front_fringe else None,
                shoulder_drop_px=float(np.median(shoulders)) if shoulders else None,
                dense_width_ratio=width_ratio, speeds_px_per_second=speed,
                min_closing_core_ratio=min(closing_ratios) if closing_ratios else None,
                cutoff_step_ratio=cutoff_ratio,
                splash_pixels_median=float(np.median(spray)) if spray else None)


def largest_exposed_patch(mask):
    """Area in native pixels of the largest four-connected patch."""
    pixels = set(zip(*np.nonzero(mask)))
    largest = 0
    while pixels:
        pending = [pixels.pop()]
        area = 0
        while pending:
            y, x = pending.pop()
            area += 1
            for neighbor in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
                if neighbor in pixels:
                    pixels.remove(neighbor)
                    pending.append(neighbor)
        largest = max(largest, area)
    return largest


def opening_edge_samples(rgb, geometry, pitch):
    """Read each column's sand edges and measure only glass behind its fringe."""
    cx, lip, floor = (round(geometry[key]) for key in ("cx", "outlet_y", "surface_y"))
    half = geometry["half_width"]
    reach = round(.91 * half)
    if reach < 5:
        raise ValueError("原帧杯口太窄，无法区分中央与两肩")
    strip = rgb[lip:floor, cx-reach:cx+reach]
    sand = gold(strip)
    n = len(sand)
    sparse = np.where(sand.any(axis=0), n - 1 - sand[::-1].argmax(axis=0), -1)
    # A few dust pixels belong to the fringe, not to the dense material.
    dense_mask = np.asarray(Image.fromarray(sand.astype(np.uint8) * 255).filter(
        ImageFilter.BoxBlur(max(1, round(pitch)))
    )) >= 230
    dense = np.where(dense_mask.any(axis=0), n - 1 - dense_mask[::-1].argmax(axis=0), -1)
    low, depth, high = np.percentile(sparse, (10, 50, 90))
    result = {"depth": float(depth), "dense": float(np.median(dense)), "fringe_span": float(high-low)}
    if not .35*half <= depth <= 1.55*half:
        return result
    back = sparse - np.arange(n)[:, None]
    band = (back >= max(2, round(3 * pitch))) & (back < round(14 * pitch))
    background = np.all(strip == geometry["background_rgb"], axis=2)
    split = round(.50 * half)
    sections = (slice(0, reach-split), slice(reach-split, reach+split), slice(reach+split, None))
    result["coverage"] = []
    result["patches"] = []
    for section in sections:
        region = band[:, section]
        exposed = background[:, section] & region
        if not region.any():
            return result
        result["coverage"].append(float(exposed.sum() / region.sum()))
        result["patches"].append(largest_exposed_patch(exposed))
    return result


def opening_reference_failures(reference, candidate, half, pitch, fps):
    """Find sustained holes, a flattened fringe or a short density transition."""
    if not reference or not candidate:
        raise ValueError("没有可比较的离口原帧")
    minimum_seconds = {"early": .05, "fringe": .08}
    observed = {"early": 0, "fringe": 0}
    runs = {}
    failures = {}
    labels = ("左肩", "中央", "右肩")

    def check(name, bad, t, minimum, details):
        if not bad:
            runs.pop(name, None)
        else:
            since = runs.setdefault(name, t)
            if t-since >= minimum - 1e-7 and name not in failures:
                failures[name] = f"{since:.3f}–{t:.3f} 秒{details}"

    for actual in candidate:
        t, depth = actual["time"], actual["depth"]
        early = .35 * half <= depth <= 1.45 * half
        fringe = 2.0 * half <= depth <= 3.6 * half
        if not early:
            for side in labels:
                runs.pop(side, None)
        if not fringe:
            runs.pop("散粒外沿", None)
            runs.pop("渐散带", None)
        if not (early or fringe):
            continue
        expected = min(reference, key=lambda f: (abs(f["depth"]-depth), abs(f["time"]-t)))
        # At 60 fps this allows the nearest genuine reference frame. Large
        # mismatches mean the frame pair cannot establish a visual result.
        if abs(expected["depth"]-depth) > .22 * half:
            runs.clear()
            continue
        if early and len(actual.get("coverage", [])) == len(expected.get("coverage", [])) == 3:
            observed["early"] += 1
            for side, ratio, normal, area in zip(labels, actual["coverage"], expected["coverage"], actual["patches"]):
                bad = ratio - normal >= .10 and area >= max(6, 6*pitch**2)
                details = (f"首落{side}沙层内连片露底，同落距露底率 "
                           f"{ratio:.1%} 对正式 3.50 的 {normal:.1%}，最大连片 {area} 个像素")
                check(side, bad, t, minimum_seconds["early"], details)
        elif early:
            for side in labels:
                runs.pop(side, None)
        if fringe:
            observed["fringe"] += 1
            span, normal = actual["fringe_span"], expected["fringe_span"]
            bad = span <= .50*normal and normal-span >= max(4*pitch, .25*half)
            details = (f"首落散粒外沿持续横齐，横向起伏 {span:.1f} 像素，"
                       f"同落距正式 3.50 为 {normal:.1f} 像素。"
                       f"密实下缘中位落距分别为 {actual['dense']:.1f} 和 "
                       f"{expected['dense']:.1f} 像素")
            check("散粒外沿", bad, t, minimum_seconds["fringe"], details)
            transition = depth - actual["dense"]
            normal = expected["depth"] - expected["dense"]
            compressed = transition <= .50*normal and normal-transition >= max(4*pitch, .25*half)
            details = (f"首落渐散带持续太短，密实下缘至散粒外沿仅 {transition:.1f} 像素，"
                       f"同落距正式 3.50 为 {normal:.1f} 像素")
            check("渐散带", compressed, t, minimum_seconds["fringe"], details)
    if any(observed[name] < 1 + math.ceil(seconds * fps) for name, seconds in minimum_seconds.items()):
        raise ValueError("同落距原帧未覆盖离口和散粒外沿的复查窗口")
    return observed, list(failures.values())


def compare_opening_reference(reference, candidate):
    """Check the 50 second opening against matching native release 3.50 frames."""
    folders = (reference, candidate)
    captures = [
        json.loads((folder / "motion.json").read_text(encoding="utf-8")) for folder in folders
    ]
    left, right = captures
    if left["version"] != "3.50" or left["period"] != 50 or left["color"] != 0:
        raise ValueError("须提供正式 3.50 的 50 秒档金沙原帧")
    for key in ("dimensions", "period", "sample_rate", "opening_seed", "grain", "color", "renderer"):
        if left[key] != right[key]:
            raise ValueError(f"首落对照条件不同：{key}")
    fps = left["sample_rate"]
    if fps < 60:
        raise ValueError("首落外沿和空洞需要至少 60 帧/秒的原幅采样")
    count = round(2*fps) + 1
    samples = [[], []]
    for folder, capture, records in zip(folders, captures, samples):
        frames = capture["frames"]
        if (not capture["completed"] or capture.get("crop_box") or len(frames) < count
                or len(frames) != capture["expected_frames"]
                or capture["source_files"] != capture["source_files_end"]):
            raise ValueError("须提供连续记录到 2 秒的完整原幅和一致的运行源码")
        for i, frame in enumerate(frames[:count]):
            hidden = frame["native_hidden"]
            t = i / fps
            if (abs(t-frame["time"]) > 1.e-7 or not hidden["IsWindow"]
                    or hidden["IsWindowVisible"] or hidden["IsIconic"]):
                raise ValueError("须在隐藏 PC 窗口从零起始连续采集原幅")
            if hashlib.sha256((folder / frame["file"]).read_bytes()).hexdigest() != frame["sha256"]:
                raise ValueError(f"{t:.3f} 秒原帧与取图记录不符")
            if frame["transfer"]["landed"] <= 1.e-12:
                rgb = frame_image(folder, frame)
                if list(rgb.shape[1::-1]) != capture["dimensions"]:
                    raise ValueError(f"{t:.3f} 秒原帧尺寸与取图记录不符")
                item = opening_edge_samples(rgb, frame["geometry"], frame["draw_state"]["pitch"])
                records.append(dict(item, time=t))
        if len(records) == count:
            raise ValueError("连续取图未覆盖首次接堆")
    half = left["frames"][0]["geometry"]["half_width"]
    pitch = left["frames"][0]["draw_state"]["pitch"]
    observed, failures = opening_reference_failures(*samples, half, pitch, fps)
    if failures:
        raise ValueError("\n".join(failures))
    return dict(opening_seed=left["opening_seed"], matched_frames=observed)


def compare_after_contact(reference, candidate):
    """Compare every frame after the physical first deposit with release 3.50."""
    folders = (reference, candidate)
    left, right = [
        json.loads((folder / "motion.json").read_text(encoding="utf-8")) for folder in folders
    ]
    if left["version"] != "3.50":
        raise ValueError("稳定流须直接对照正式 3.50")
    for key in ("dimensions", "period", "sample_rate", "opening_seed", "grain", "color", "renderer"):
        if left[key] != right[key]:
            raise ValueError(f"对照条件不同：{key}")
    for capture in (left, right):
        frames = capture["frames"]
        if (
            not capture["completed"]
            or len(frames) != capture["expected_frames"]
            or not frames
            or capture.get("crop_box")
            or capture["sample_rate"] <= 0
            or capture["source_files"] != capture["source_files_end"]
        ):
            raise ValueError("须提供完整的原幅取图和一致的运行源码")
    if len(left["frames"]) != len(right["frames"]):
        raise ValueError("原帧数量不同")
    first = None
    compared = 0
    for index, (a, b) in enumerate(zip(left["frames"], right["frames"])):
        t = index / left["sample_rate"]
        if any(
            abs(f["time"] - t) > 1.0e-7
            or not f["native_hidden"]["IsWindow"]
            or f["native_hidden"]["IsWindowVisible"]
            for f in (a, b)
        ):
            raise ValueError("必须在隐藏窗口从零起步，以相同采样率连续取图")
        if a["geometry"] != b["geometry"] or a["transfer"] != b["transfer"]:
            raise ValueError(f"{t:.3f} 秒几何或沙量与正式 3.50 不一致")
        if first is None:
            if a["transfer"]["landed"] <= 1.0e-12:
                continue
            first = t
        hashes = []
        for folder, frame in zip(folders, (a, b)):
            digest = hashlib.sha256((folder / frame["file"]).read_bytes()).hexdigest()
            if digest != frame["sha256"]:
                raise ValueError(f"{t:.3f} 秒原帧与取图记录不符")
            hashes.append(digest)
        if hashes[0] != hashes[1] and not np.array_equal(
            frame_image(reference, a), frame_image(candidate, b)
        ):
            raise ValueError(f"{t:.3f} 秒接堆后的画面偏离正式 3.50")
        compared += 1
    if not compared:
        raise ValueError("原帧未覆盖首次接堆")
    return dict(
        contact_seconds=first, identical_frames=compared, last_seconds=left["frames"][-1]["time"]
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--after-contact-reference", type=Path, help="逐帧对比正式 3.50 首次接堆后的原幅录像"
    )
    mode.add_argument(
        "--opening-reference", type=Path, help="同落距对比正式 3.50 首落的局部露底、散粒外沿和渐散带"
    )
    mode.add_argument("--opening-only", action="store_true", help="仅检查从开场到首次触地")
    args = parser.parse_args()
    try:
        if args.after_contact_reference:
            result = compare_after_contact(args.after_contact_reference, args.capture)
        elif args.opening_reference:
            result = compare_opening_reference(args.opening_reference, args.capture)
        else:
            result = review(args.capture, args.opening_only)
        print(json.dumps(result, ensure_ascii=False))
    except ValueError as error:
        parser.exit(1, f"{error}\n")
