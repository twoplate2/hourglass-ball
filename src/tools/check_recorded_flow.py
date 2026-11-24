"""Review original gold-sand frames from capture_stream_motion.py on a hidden PC."""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


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
    sphere_endpoint(initial_image, geo, upper=True)
    gap = crown_gap(initial_image, geo)
    if gap > max(2., .007*radius):
        raise ValueError(f"待机上杯顶部可见 {gap} 像素空隙")

    contact = None
    above, times, masks, luminance, spray, closing_ratios = [], [], [], [], [], []
    tail_fringe, cutoff_samples = [], []
    head_frames, head_tips = [], []
    for frame in frames:
        t = frame["time"]
        if t >= period + .25 and frame["transfer"]["landed"] >= 1. - 1e-8:
            sphere_endpoint(frame_image(folder, frame), geo, upper=False)
            continue
        layer = (1. - t/period) * (4.*radius**3/3.) / half**3
        closing = 0. < layer < .65
        released_tail = period == 15 and 0. < t-period <= .05 and frame["transfer"]["flight"] > 1e-9
        cutoff = period in (5,15) and abs(t-period) < .12
        if contact is None or closing or cutoff or (.24*period < t < .60*period) or (3 <= t <= 4.5):
            rgb = frame_image(folder, frame)
            mask = gold(rgb)
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
            elif period in (1,5,15):
                central = mask[lip:floor, cx-2:cx+3]
                tip = floor-1 - int(np.median(np.argmax(central[::-1], axis=0)))
                if lip+.18*(floor-lip) < tip < lip+.85*(floor-lip):
                    head_frames.append(rgb[:,:,1])
                    head_tips.append(tip)
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
    if period in windows:
        low, high = windows[period]
        if not low <= contact <= high + 1/meta["sample_rate"]:
            raise ValueError(f"{period:g} 秒档首落 {contact:.3f} 秒")
    if opening_only:
        return dict(period=period, crown_gap_px=gap, contact_seconds=contact)
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
                dense_width_ratio=width_ratio, speeds_px_per_second=speed,
                min_closing_core_ratio=min(closing_ratios) if closing_ratios else None,
                cutoff_step_ratio=cutoff_ratio,
                splash_pixels_median=float(np.median(spray)) if spray else None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--opening-only", action="store_true", help="仅检查从开场到首次触地")
    args = parser.parse_args()
    try:
        print(json.dumps(review(args.capture, args.opening_only), ensure_ascii=False))
    except ValueError as error:
        parser.exit(1, f"{error}\n")
