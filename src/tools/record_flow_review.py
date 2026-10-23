"""Read-only real-time capture using the existing native hidden harness."""
import argparse
import hashlib
import json
from fractions import Fraction
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src/tools'))
import verify_short_grain_runtime as harness
import record_short_grain

p=argparse.ArgumentParser()
p.add_argument('--period',type=float,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--pause',action='store_true')
p.add_argument('--grain',type=int,choices=range(1,7),default=3)
p.add_argument('--color',type=int,choices=range(6),default=0)
args=p.parse_args()
output=args.output.resolve()
output.parent.mkdir(parents=True,exist_ok=True)
cases=output.parent/(output.name+'-cases.json')
cases.write_text(json.dumps([dict(name='initial',period=args.period,elapsed=0,state='initial',frame_offsets=[0])]),encoding='utf-8')

def record_audit(w, out, advance, _capture, hidden_check):
    import av
    import numpy as np
    from PIL import Image
    from kivy.graphics import Fbo,ClearColor,ClearBuffers
    from kivy.graphics.transformation import Matrix
    game=sys.modules[w.__class__.__module__]
    helper=sys.modules['pc_material_probe_helpers']
    w.set_grain_level(str(args.grain))
    _,base,dark,light=game.SAND_PRESETS[args.color]
    w.set_sand_color(base,dark,light)
    w.set_duration(args.period)
    w.reset()
    w._rebuild_height_table()
    while w._warm_queue:w._warm_batches_step()
    random.seed(helper.SEED)
    np.random.seed(helper.SEED)
    w._front_seed=.75
    w.last_frame,w.last_tick=helper.EPOCH,None
    width,height=int(w.width),int(w.height)
    fbo=Fbo(size=(width,height),with_stencilbuffer=True)
    fbo['projection_mat']=Matrix().view_clip(0,width,0,height,-1,1,0)
    fbo['modelview_mat']=Matrix()
    with fbo:ClearColor(*game.hex_rgb(game.BG_COLOR),1);ClearBuffers()
    def pixels():
        w.redraw()
        fbo.add(w.canvas)
        try:fbo.draw();raw=bytes(fbo.pixels)
        finally:fbo.remove(w.canvas)
        return np.frombuffer(raw,dtype=np.uint8).reshape(height,width,4)[::-1,:,:3].copy()
    Image.fromarray(pixels()).save(out/'zero.png')
    movie=out/'audit.mp4'
    container=av.open(str(movie),'w')
    stream=container.add_stream('libx264',rate=60)
    stream.width,stream.height=width,height
    stream.pix_fmt='yuv420p'
    stream.options={'crf':'18','preset':'veryfast'}
    tb=Fraction(1,60000)
    stream.time_base=tb
    stream.codec_context.time_base=tb
    outlet=2*w._neck_y-w._taper['y_bot']
    report=dict(completed=False,scope='real-time hidden PC video audit; not visual or FPS acceptance',
                period=args.period,grain=args.grain,color=args.color,pause_enabled=args.pause,version=game.APP_VERSION,dimensions=[width,height],seed=helper.SEED,
                geometry=dict(cx=w._cx,radius=w._R_inner,upper_cy=height-w._upper_y_c,
                    lower_cy=height-w._lower_y_c,outlet_y=height-outlet,neck_width=2*w._taper['t_in'],
                    background_rgb=[255*v for v in game.hex_rgb(game.GLASS_FILL)]),
                frames=[],movie=movie.name)
    def save():
        (out/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    save()
    w.toggle()
    began=time.perf_counter()
    deadline=args.period+max(1.5,.1*args.period)
    checkpoint=0
    paused=resumed=False
    pause_at=.94*args.period
    pause_length=.5 if args.period<=5 else 2.0
    if args.pause:deadline+=pause_length
    try:
        while True:
            t=time.perf_counter()-began
            advance(t)
            if args.pause and t>=pause_at and not paused:
                w.toggle();paused=True
            if args.pause and t>=pause_at+pause_length and not resumed:
                w.toggle();resumed=True
            hidden=hidden_check()
            image=pixels()
            vf=av.VideoFrame.from_ndarray(image,format='rgb24')
            vf.pts=round(t*60000);vf.time_base=tb
            for packet in stream.encode(vf):container.mux(packet)
            apex=w._mound_apex()
            surface=w.get_mound_top_y()
            report['frames'].append(dict(time=t,elapsed=w.elapsed,running=bool(w.running),
                particles=w.pn,splashes=w._sn,transfer=w.sand_transfer_state(),
                surface_y=height-surface,gap=outlet-surface,
                center_free=bool(w._mound_profile.column(0,apex)[1]),
                splash_stats=dict(w._splash_stats),hidden=hidden,
                image_sha256=hashlib.sha256(image.tobytes()).hexdigest(),
                receiving_active=bool(w._mound_surface_flow and w._mound_surface_flow._node_count),
                contact_sha256=hashlib.sha256(image[max(0,int(height-surface-8)):min(height,int(height-surface+20)),max(0,int(w._cx-2*w._contact_flow_diameter())):min(width,int(w._cx+2*w._contact_flow_diameter()))].tobytes()).hexdigest()))
            if int(t)>checkpoint:
                checkpoint=int(t);save()
                if checkpoint%5==0:print('AUDIT_SECOND',checkpoint,flush=True)
            if t>=deadline:break
            time.sleep(max(0,1/60-(time.perf_counter()-began-t)))
        for packet in stream.encode():container.mux(packet)
        container.close()
        report.update(completed=True,duration=report['frames'][-1]['time'],
            pause_at=pause_at if args.pause else None,pause_length=pause_length if args.pause else 0,
            ended_settled=w.pn==0 and w._sn==0 and not w.running,
            sha256=hashlib.sha256(movie.read_bytes()).hexdigest())
        if not report['ended_settled']:raise RuntimeError('Video ended before settling')
        save()
    except BaseException:
        try:container.close()
        finally:save()
        raise
    return {k:v for k,v in report.items() if k!='frames'}

record_short_grain.record=record_audit
sys.argv=['verify_short_grain_runtime.py','--runtime',str(ROOT),'--output',str(output),
          '--width','540','--height','924','--cases',str(cases),'--video']
harness.main()
