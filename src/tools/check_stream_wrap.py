"""Detect a visible phase-reset discontinuity in actual adjacent GL frames."""
import json
from pathlib import Path

import numpy as np
from PIL import Image


def inspect(report_path):
    report_path=Path(report_path)
    r=json.loads(report_path.read_text(encoding="utf-8"))
    if not r.get("completed") or len(r.get("frames",[]))<20:
        raise ValueError("Incomplete wrap sequence")
    frames=r["frames"];boundary=r["boundary_time"]
    if not frames[0]["time"]<boundary<frames[-1]["time"]:
        raise ValueError("Sequence does not cross phase boundary")
    deltas=[];cross=None
    for i,(a,b) in enumerate(zip(frames,frames[1:])):
        if b["time"]<=a["time"]:raise ValueError("Invalid frame timestamps")
        g=a["geometry"]
        x0=int(g["cx"]-g["half_width"]*1.3);x1=int(g["cx"]+g["half_width"]*1.3)
        y0=int(g["outlet_y"]);y1=int(min(g["surface_y"],b["geometry"]["surface_y"]))
        if y1-y0<8:raise ValueError("No visible stream at phase boundary")
        aa=np.asarray(Image.open(report_path.parent/a["file"]).convert("RGB"))[y0:y1,x0:x1].astype(float)
        bb=np.asarray(Image.open(report_path.parent/b["file"]).convert("RGB"))[y0:y1,x0:x1].astype(float)
        deltas.append(float(np.abs(aa-bb).mean()))
        if a["time"]<boundary<=b["time"]:cross=i
    reference=float(np.median(deltas))
    if reference<=0.01:raise ValueError("Frozen frames cannot prove moving continuity")
    ratio=deltas[cross]/reference
    return dict(passed=bool(ratio<2),boundary_type=r["boundary_type"],boundary_time=boundary,
                boundary_delta=deltas[cross],median_delta=reference,ratio=ratio,deltas=deltas)
