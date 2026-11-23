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
    for dy in range(int(np.ceil(dt * .68 * radius)) + 2):
        moved = b[row-height+dy:row+height+dy+1, x].astype(float)
        moved -= moved.mean()
        scores.append(float((template*moved).sum() / max(1e-9, norm*np.linalg.norm(moved))))
    best = int(np.argmax(scores))
    if not 0 < best < len(scores)-1 or scores[best] < .40:
        return None
    left, peak, right = scores[best-1:best+2]
    shift = .5 * (left-right) / (left+right-2*peak) if left+right != 2*peak else 0.
    return (best + shift) / dt


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


def review(folder):
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
    gap = crown_gap(frame_image(folder, frames[0]), geo)
    if gap > max(2., .007*radius):
        raise ValueError(f"待机上杯顶部可见 {gap} 像素空隙")

    contact = None
    above, times, masks, luminance, spray = [], [], [], [], []
    for frame in frames:
        t = frame["time"]
        if contact is None or (.24*period < t < .60*period) or (3 <= t <= 4.5):
            rgb = frame_image(folder, frame)
            mask = gold(rgb)
        else:
            continue
        if contact is None:
            bottom = mask[floor-max(2,round(.009*radius)):floor+1, cx-round(half):cx+round(half)+1]
            if bottom.any():
                contact = t
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
        hop = max(1, round(.09 * meta["sample_rate"]))
        for depth in (.045, .47, .93):
            values = []
            for i in range(len(times)-hop):
                dt = times[i+hop] - times[i]
                v = motion_speed(luminance[i], luminance[i+hop], dt, cx,
                                 lip+round(depth*radius), half, radius)
                if v is not None:
                    values.append(v)
            if len(values) < 5:
                raise ValueError("纹理追踪缺少连续、可信的位移")
            speed.append(round(float(np.median(values)), 2))
        if not (speed[0] < speed[1] < speed[2] and 1.10 < speed[2]/speed[0] < 1.47):
            raise ValueError(f"柱内三处纹理速度 {speed} 像素/秒")
    return dict(period=period, crown_gap_px=gap, contact_seconds=contact,
                dense_width_ratio=width_ratio, speeds_px_per_second=speed,
                splash_pixels_median=float(np.median(spray)) if spray else None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    try:
        print(json.dumps(review(parser.parse_args().capture), ensure_ascii=False))
    except ValueError as error:
        parser.exit(1, f"{error}\n")
