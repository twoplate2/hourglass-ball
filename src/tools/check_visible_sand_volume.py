"""Measure gold sand in full-size capture_stream_motion PNGs, independent of the ledger.

Capture from t=0 through settling. Each image column represents half a ring
of an axially symmetric glass. Count the two sides separately, including the
free jet and its gaps. This estimates occupied volume from a projection.
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image


VISIBLE_VOLUME_ERROR = .025  # fraction of the initial packed volume
PHYSICS_STEP = 1. / 120.  # logical time step in capture_stream_motion


def measure(capture):
    meta = json.loads((capture / "motion.json").read_text(encoding="utf8"))
    frames = meta["frames"]
    if (not meta["completed"] or meta["color"] != 0 or meta.get("crop_box")
            or len(frames) < 2 or frames[0]["time"] != 0
            or len(frames) != meta["expected_frames"] or meta["sample_rate"] <= 0
            or any(abs(f["time"] - i / meta["sample_rate"]) > 1e-7
                   for i, f in enumerate(frames))):
        raise ValueError("需要从满杯开始、正常尺寸的完整金沙画面")
    size = meta["dimensions"]
    geo = frames[0]["geometry"]
    radius = geo["radius"]
    offsets = np.arange(size[0]) + .5 - geo["cx"]
    rings = np.where(abs(offsets) <= radius + 1, abs(offsets), 0.)
    outlet = round(geo["outlet_y"])
    floor = round(geo["surface_y"])
    jet = abs(offsets) <= geo["half_width"]
    connected = False
    volumes = []
    for frame in frames:
        with Image.open(capture / frame["file"]) as image:
            rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
        gold = (rgb[:, :, 0] - rgb[:, :, 2] > 45) & (rgb[:, :, 0] - rgb[:, :, 1] > 14)
        if not connected:
            # A large pre-drawn heap could hide a missing jet's mass in the
            # total. Look for a deposit on the floor while the jet is detached.
            filled = np.flatnonzero(gold[outlet:floor, jet].any(axis=1))
            gap = np.diff(np.r_[-1, filled, floor - outlet]).max() - 1
            connected = gap <= max(3, .02 * radius)
            bed = gold[floor-round(.05*radius):floor, jet]
            if not connected and bed.mean() > .30:
                raise ValueError(f"{frame['time']:.2f} 秒底部已经成堆，沙柱仍与落点分离")
        above = float(np.dot(gold[:outlet].sum(axis=0), rings))
        below = float(np.dot(gold[outlet:].sum(axis=0), rings))
        volumes.append((above, below))
    capacity = volumes[0][0]
    if not .95 < capacity / (4. * radius**3 / 3.) < 1.05 or volumes[0][1]:
        raise ValueError("首帧应当是满上杯、空下杯")
    return [(f["time"], a/capacity, b/capacity) for f, (a, b) in zip(frames, volumes)]


def check(samples):
    worst = max(samples, key=lambda s: abs(s[1] + s[2] - 1.))
    if abs(worst[1] + worst[2] - 1.) > VISIBLE_VOLUME_ERROR:
        raise ValueError(f"{worst[0]:.2f} 秒，可见沙量占初始量的 {worst[1]+worst[2]:.1%}，"
                         f"出口以上 {worst[1]:.1%}，出口以下 {worst[2]:.1%}")
    if samples[-1][1] > .001 or abs(samples[-1][2] - 1.) > .005:
        raise ValueError(f"落定后下杯为开场沙量的 {samples[-1][2]:.2%}，初末允差为半个百分点")
    return worst


def check_outlet_flow(samples, duration, *, pixels=False):
    """Check both sides of the outlet against one full bulb per selected period.

    Time starts at the start button. Samples must include the deadline and tail.
    Above includes the neck. Below includes both flying and deposited sand.
    """
    data = np.asarray(samples, dtype=float)
    if (not math.isfinite(duration) or duration <= 0 or data.ndim != 2
            or data.shape[1] != 3 or len(data) < 2 or not np.isfinite(data).all()):
        raise ValueError("需要有限的档位时长和完整的逐帧沙量")
    times, above, below = data.T
    window = min(1., duration / 4.)
    if (times[0] != 0. or times[-1] < duration + PHYSICS_STEP - 1e-9
            or np.any(np.diff(times) <= 0.) or np.max(np.diff(times)) > window / 2. + 1e-9):
        raise ValueError("取样必须从按下开始连续覆盖整档和到点以后，并能分辨检查窗口")
    if pixels:
        # Accumulate at least 10% of the bulb when a long timer's one-second
        # signal is too small for this projection. The ledger still uses 1 s.
        window = max(window, 4. * VISIBLE_VOLUME_ERROR * duration)
        cumulative_error = window_error = VISIBLE_VOLUME_ERROR
    else:
        cumulative_error = PHYSICS_STEP / duration + 1e-9
        window_error = 2. * cumulative_error

    # Use observed frames on both sides of each window. If the deadline falls
    # between frames, the target stops there even though the next frame is later.
    starts = np.flatnonzero(times < duration)
    ends = np.searchsorted(times, np.minimum(times[starts] + window, duration) - 1e-9)
    expected = np.minimum(times / duration, 1.)
    target = expected[ends] - expected[starts]
    errors = []
    for name, discharged in (("出口以上减少", 1. - above), ("出口以下增加", below)):
        delta = discharged[ends] - discharged[starts]
        i = np.argmax(np.abs(discharged - expected))
        j = np.argmax(np.abs(delta - target))
        if abs(discharged[i] - expected[i]) > cumulative_error:
            errors.append(f"{name}，{times[i]:.2f} 秒累计 {discharged[i]:.2%}，应为 {expected[i]:.2%}")
        if abs(delta[j] - target[j]) > window_error:
            errors.append(f"{name}，{times[starts[j]]:.2f}–{times[ends[j]]:.2f} 秒 "
                          f"{delta[j]:.2%}，应为 {target[j]:.2%}")
        if not pixels and np.any(np.abs(discharged[times >= duration + PHYSICS_STEP - 1e-9] - 1.) > 1e-8):
            errors.append(f"{name}，计时到点后仍有沙未通过杯口")
    if errors:
        raise ValueError("；".join(errors))
    return window


def check_capture(capture):
    meta = json.loads((capture / "motion.json").read_text(encoding="utf8"))
    samples = measure(capture)
    errors = []
    try:
        check(samples)
    except ValueError as error:
        errors.append(f"可见总量：{error}")
    try:
        check_outlet_flow(samples, meta["period"], pixels=True)
    except ValueError as error:
        errors.append(f"可见流量：{error}")
    frames = meta["frames"]
    if all("transfer" in frame for frame in frames):
        ledger = []
        for frame in frames:
            state = frame["transfer"]
            amounts = [state[k] for k in ("upper", "neck", "flight", "landed")]
            if (any(not math.isfinite(v) or v < -1e-9 for v in amounts)
                    or abs(sum(amounts) - 1.) > 1e-9):
                errors.append(f"运行记录：{frame['time']:.2f} 秒的沙量不守恒")
                break
            ledger.append((frame["time"], state["upper"] + state["neck"],
                           state["flight"] + state["landed"]))
        else:
            try:
                check_outlet_flow(ledger, meta["period"])
            except ValueError as error:
                errors.append(f"逐秒出沙量：{error}")
            if frames[-1]["transfer"]["landed"] < 1. - 1e-8:
                errors.append("取证结束时仍有沙没有落定")
    else:
        errors.append("缺少逐帧运行沙量，仅可复核原图")
    if errors:
        raise ValueError("\n".join(errors))
    return len(samples)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    try:
        count = check_capture(parser.parse_args().capture)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(f"已检查 {count} 帧的总量、杯口逐秒出沙量和可见体积变化")
