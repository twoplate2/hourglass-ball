"""Isolate near-full surface animation on frozen actual PC geometry."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/tools'))
import record_short_grain
import verify_short_grain_runtime as harness


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--renderer', choices=('texture', 'native'), default='texture')
    args=p.parse_args();out=args.output.resolve();out.parent.mkdir(parents=True,exist_ok=True)
    cases=out.parent/(out.name+'-cases.json')
    cases.write_text(json.dumps([dict(name='initial',period=50,elapsed=0,state='initial',frame_offsets=[0])]),encoding='utf-8')

    def record(w,dest,advance,capture,hidden_check):
        import numpy as np
        from PIL import Image
        from kivy.graphics import Fbo,ClearColor,ClearBuffers
        from kivy.graphics.transformation import Matrix
        game=sys.modules[w.__class__.__module__]
        # Observe the real surface while its material is still moving.
        for step in range(61):
            advance(w.duration - 1. + step/60.)
            w.redraw()
            center_free = bool(w._mound_profile.column(0, w._mound_apex())[1])
            headroom = w._lower_sand_top - w.get_mound_top_y()
            if (0. < headroom < .04*w._R_inner if args.renderer == 'native' else not center_free):
                break
        else:
            raise ValueError('未找到落点表层仍在运动的近满杯画面')
        ctx=w._mound_surface_flow
        if not ctx or not ctx._node_count:raise ValueError('Receiving mesh is absent')
        lower_rect = w._sand_chords[1][1]
        tex_bottom, tex_height = ctx['sand_geometry'][2:]
        if (abs(tex_bottom - lower_rect.pos[1]) > .01
                or abs(tex_height - lower_rect.size[1]) > .01):
            raise ValueError('落点表层与下杯沙体使用不同的纹理高度，杯颈会出现拉纹')
        width,height=int(w.width),int(w.height)
        saved_mix,saved_phase=ctx['sand_mix'],ctx['surface_phase']
        saved_indices=list(ctx._mesh.indices)
        images={};hashes={}
        try:
            for name,phase,mix in [('on-a',0.,saved_mix),('on-b',.25,saved_mix),('off-a',0.,0),('off-b',.25,0)]:
                ctx['surface_phase']=phase;ctx['sand_mix']=mix
                ctx._mesh.indices=saved_indices if mix else []
                ctx.ask_update();w.canvas.ask_update()
                fbo=Fbo(size=(width,height),with_stencilbuffer=True)
                fbo['projection_mat']=Matrix().view_clip(0,width,0,height,-1,1,0)
                fbo['modelview_mat']=Matrix()
                with fbo:ClearColor(*game.hex_rgb(game.BG_COLOR),1);ClearBuffers()
                fbo.add(w.canvas)
                try:fbo.draw();raw=bytes(fbo.pixels)
                finally:fbo.remove(w.canvas)
                im=Image.frombytes('RGBA',(width,height),raw).transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert('RGB')
                im.save(dest/(name+'.png'));images[name]=np.asarray(im).astype(int)
                hashes[name]=hashlib.sha256((dest/(name+'.png')).read_bytes()).hexdigest()
        finally:
            ctx['sand_mix']=saved_mix;ctx['surface_phase']=saved_phase;ctx._mesh.indices=saved_indices
        from check_flow_release import require_receiving_arrays
        measured=require_receiving_arrays(images)
        report=dict(completed=True,elapsed=w.elapsed,hidden=hidden_check(),
            method='fixed geometry and all other clocks; receiving phases 0 and .25; disabled-mesh negative',
            center_free=center_free, headroom_px=headroom,
            images=hashes,**measured)
        (dest/'receiving.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        return report

    record_short_grain.record=record
    sys.argv=['verify_short_grain_runtime.py','--runtime',str(ROOT),'--output',str(out),
              '--width','540','--height','924','--cases',str(cases),'--video','--renderer',args.renderer]
    harness.main()


if __name__=='__main__':main()
