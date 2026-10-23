"""Actual GL pixel motion at fine logical sampling; not display FPS.

The high sampling rate makes small grain displacement measurable. This checks
local advection, not ownership of every pixel or normal-speed perceptual quality.
"""
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image


def match_shift(a,b,pad=6):
    reference=a[pad:-pad].astype(float)
    reference-=reference.mean()
    denom=float(np.sqrt((reference*reference).sum()))
    if denom<.01:raise ValueError('No measurable grain detail')
    scores=[]
    for shift in np.arange(-pad,pad+.01,.25):
        integer=math.floor(shift);fraction=shift-integer
        start=pad+integer;length=len(b)-2*pad
        patch=b[start:start+length].astype(float)
        if fraction:
            patch=(1-fraction)*patch+fraction*b[start+1:start+length+1]
        patch-=patch.mean()
        scale=float(np.sqrt((patch*patch).sum()))*denom
        score=float((reference*patch).sum())/scale if scale>1e-9 else -1
        scores.append((score,shift))
    score,shift=max(scores)
    return float(shift),score


def inspect(path):
    path=Path(path);r=json.loads(path.read_text(encoding='utf-8'))
    if not r['completed'] or r['sample_rate']!=240 or len(r['frames'])!=61:
        raise ValueError('Missing high-sampling actual GL pixels')
    g=r['frames'][0]['geometry'];width=2*g['half_width']
    y=int(g['outlet_y']+min(3*width,.45*(g['surface_y']-g['outlet_y'])))
    x=int(g['cx']);half=max(5,int(.3*width));height=max(20,int(.8*width))
    previous=None;results=[]
    expected=math.sqrt(50**2+900*(y+height/2-g['outlet_y']))/240
    for f in r['frames']:
        p=path.parent/f['file']
        if hashlib.sha256(p.read_bytes()).hexdigest()!=f['sha256']:raise ValueError('Pixel frame changed')
        with Image.open(p) as im:a=np.asarray(im.convert('RGB'),dtype=float)
        tile=a[y:y+height+12,x-half:x+half].mean(2)
        if previous is not None:
            shift,score=match_shift(previous,tile)
            results.append(dict(shift=shift,score=score))
        previous=tile
    good=[d for d in results if d['score']>=.4 and d['shift']>0 and abs(d['shift']-expected)<=1]
    if len(good)<.8*len(results) or any(d['score']>.8 and d['shift']<=0 for d in results):
        raise ValueError('Pixels do not consistently follow downward grain motion: '+str(results))
    return dict(pairs=len(results),matched=len(good),expected_shift=expected,
                min_score=min(d['score'] for d in results),rows=results)


if __name__=='__main__':
    import sys
    print(json.dumps(inspect(sys.argv[1])))
