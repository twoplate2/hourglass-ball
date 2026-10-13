# -*- coding: utf-8 -*-
"""A2: `_stream_grain_idx` 的量纲 —— `_flow_lum`(0..1) vs `tgt`(0..255)."""
import os, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import main as m
w = m.HourglassWidget.__new__(m.HourglassWidget)
# 手工跑一遍 _rebuild_color_table 需要一堆实例属性; 直接算表(与代码逐字同式)
import importlib
# 用一个真 app 更稳: 只建 widget 太重, 这里照抄算式:
from app_version import APP_VERSION  # noqa
# 拿 SAND_PRESETS[0] 的 base/dark/light
name, base, dark, light = m.SAND_PRESETS[0]
print("基色 base=", base, " light=", light)
flow_table = []
for k in range(11):
    d = (k-5)*m.FLOW_TONE_STEP
    if d == 0.0: flow_table.append(base); continue
    end = light if d>0 else dark
    span = abs(end[0]-base[0])*255.0
    t0=base[0]; t1=end[0]
    t = min(1.0, abs(d)/max(1e-6,span))
    flow_table.append(tuple(base[i]+(end[i]-base[i])*t for i in range(3)))
_lw=(0.299,0.587,0.114)
flow_lum = tuple(c[0]*_lw[0]+c[1]*_lw[1]+c[2]*_lw[2] for c in flow_table)
print("_flow_lum (代码现值) =", ["%.4f"%v for v in flow_lum])
mat_lum_base = (base[0]*0.299+base[1]*0.587+base[2]*0.114)*255.0
print("_mat_lum_base =", round(mat_lum_base,1))
tgt = mat_lum_base + 0.0
print("tgt(材质≈base) =", round(tgt,1))
idx = int(np.clip(np.searchsorted(np.asarray(flow_lum), tgt), 0, 10))
print("→ searchsorted 结果 idx =", idx, "(=11 被 clip 到 10)")
flow_lum_255 = tuple(v*255.0 for v in flow_lum)
print("若按 0..255 尺度(×255): _flow_lum =", ["%.0f"%v for v in flow_lum_255])
for g in (-20, -5, 0, 5, 20):
    ii = int(np.clip(np.searchsorted(np.asarray(flow_lum_255), mat_lum_base+g), 0, 10))
    print("   材质偏离 %+3d 级 → idx=%d" % (g, ii))
