"""Default-gold picture counterexamples; metrics assist, never replace review.

These measurements deliberately do not claim color-independent segmentation or
particle identity from pixels. Other colors are checked by the capture matrix
and reviewed actual pictures. Ignore short gaps where pile/neck dominate.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


def longest_run(mask):
    best=current=0
    for value in mask:
        current=current+1 if value else 0
        best=max(best,current)
    return best


def inspect_frame(pixels,geometry):
    a=np.asarray(pixels,dtype=np.int16)
    cx=float(geometry['cx']);out=float(geometry['outlet_y']);bottom=float(geometry['surface_y'])
    width=2*float(geometry['half_width']);gap=bottom-out
    if gap<3*width:return dict(skipped='short contact gap',failures=[])
    h,w=a.shape[:2]
    y0=max(0,int(out));y1=min(h,int(bottom-.3*width))
    x0=max(0,int(cx-width));x1=min(w,int(cx+width)+1)
    region=a[y0:y1,x0:x1]
    sand=(region[:,:,0]>region[:,:,1]+12)&(region[:,:,1]>region[:,:,2]+20)
    xs=np.arange(x0,x1)
    counts=sand.sum(1)
    extents=np.where(sand,xs[None,:],np.nan)
    active=counts>0
    widths=np.zeros(len(counts));centres=np.full(len(counts),cx)
    if active.any():
        lo=np.nanmin(extents[active],axis=1);hi=np.nanmax(extents[active],axis=1)
        widths[active]=hi-lo+1
        centres[active]=(lo+hi)*.5
    neck_band=max(1,round(.5*width))
    top_width=float(widths[:neck_band].max(initial=0))
    begin=max(0,int(.2*gap));end=min(len(counts),int(.75*gap))
    core=(xs>=cx-.35*width)&(xs<=cx+.35*width)
    core_rows=sand[begin:end,core].mean(1)
    coverage=float(core_rows.mean())
    middle_widths=widths[begin:end]
    roughness=float(np.std(middle_widths))
    center=float(np.median(centres[begin:end])-cx)
    holes=longest_run(core_rows<.10)
    failures=[]
    if coverage<.40:failures.append('invisible')
    if top_width>width+max(1.5,.06*width):failures.append('exit_expands')
    if coverage>.985 and roughness<.45:failures.append('solid_rod')
    if holes>max(3,.20*width):failures.append('empty_layer')
    return dict(failures=failures,neck_width=width,exit_width=top_width,
        core_coverage=coverage,width_roughness=roughness,center_offset=center,
        empty_run_pixels=holes)


def require_sequence(rows,coarse=False):
    active=[r for r in rows if not r.get('skipped')]
    if not active:raise ValueError('No independent free-stream pictures')
    if any(r['failures'] for r in active):
        raise ValueError('Picture counterexample: '+str(next(r for r in active if r['failures'])))
    if any(abs(r['center_offset'])>.12*r['neck_width']+1 for r in active):
        raise ValueError('Whole-stream sway exceeds the local edge budget')
    if coarse and sum(r['core_coverage'] for r in active)/len(active)>.995:
        raise ValueError('Coarse interior remains continuously overfilled despite rough edges')
    return dict(frames=len(active),max_exit_ratio=max(r['exit_width']/r['neck_width'] for r in active),
        min_coverage=min(r['core_coverage'] for r in active),max_empty_run=max(r['empty_run_pixels'] for r in active),
        max_center_offset=max(abs(r['center_offset']) for r in active))


def inspect_motion(path):
    path=Path(path);report=json.loads(path.read_text(encoding='utf-8'))
    if not report.get('completed') or report.get('source_files')!=report.get('source_files_end'):
        raise ValueError('Capture incomplete or sources changed')
    frames=report['frames']
    if len(frames)!=report.get('expected_frames'):raise ValueError('Missing real frames')
    rows=[]
    for f in frames:
        p=path.parent/f['file']
        if hashlib.sha256(p.read_bytes()).hexdigest()!=f['sha256']:raise ValueError('Frame changed')
        with Image.open(p) as im:rows.append(inspect_frame(im.convert('RGB'),f['geometry']))
    return require_sequence(rows,coarse=report.get('grain',0)>=6)
