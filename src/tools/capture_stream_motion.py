"""Real hidden PC GL sequence with exact per-frame geometry and logical time.

This is a fixed-step appearance probe, not a frame-rate benchmark or wall-clock
recording. Playback timestamps equal the actual simulation timestamps supplied.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import time

from verify_short_grain_runtime import load_module, native_hidden_check, source_manifest

ROOT = Path(__file__).resolve().parents[2]


def capture(args):
    args.output.mkdir(parents=True, exist_ok=False)
    helper = load_module("motion_capture_helpers", ROOT / "src/tools/material_visual_probe.py")
    report = dict(completed=False, frames=[], timing="fixed logical steps; real GL pixels; not FPS",
                  source_files=source_manifest(args.runtime, helper), dimensions=[args.width,args.height])
    target = args.output / "motion.json"
    def save():
        target.write_text(json.dumps(report,indent=2),encoding="utf-8")
    save()
    with tempfile.TemporaryDirectory(dir=args.output, prefix="isolated-") as isolated:
        for k in list(os.environ):
            if k.startswith("HG_"): del os.environ[k]
        os.environ.update(KIVY_HOME=isolated,KIVY_NO_ARGS="1",KIVY_NO_CONFIG="1",KIVY_NO_FILELOG="1",
                          KIVY_WINDOW="sdl2",KIVY_METRICS_DENSITY="1",KIVY_METRICS_FONTSCALE="1",
                          HG_SPLASH_RENDERER="batch",HG_NECK_RENDERER="batch")
        sys.dont_write_bytecode=True
        from kivy.config import Config
        Config.set("graphics","window_state","hidden")
        Config.set("graphics","width",str(args.width));Config.set("graphics","height",str(args.height))
        from kivy.core.window import Window
        from kivy.clock import Clock
        from kivy.graphics import Fbo,ClearColor,ClearBuffers
        from kivy.graphics.transformation import Matrix
        from PIL import Image
        report["hidden_at_creation"]=native_hidden_check(Window,Config)
        try:
            sys.path[:0]=[str(args.runtime/"src/app"),str(args.runtime/"src"),str(args.runtime)]
            game=load_module("motion_runtime",args.runtime/"main.py")
            now=[helper.EPOCH]
            class FakeTime:
                @staticmethod
                def perf_counter(): return now[0]
                def __getattr__(self,name):return getattr(time,name)
            game.time=FakeTime()
            game.config_path=lambda:str(Path(isolated)/"settings.json")
            for name in ("_make_sound_proxy","_make_completion_sound","_load_voice_bank",
                         "_play_sound","_play_completion_sound","_voice_say","_schedule_completion_popup"):
                setattr(game.HourglassWidget,name,helper.named_noop(name))
            random.seed(helper.SEED)
            w=game.HourglassWidget(size=(args.width,args.height),pos=(0,0))
            for fn in (w.tick,w._load_voice_bank,w._on_size):Clock.unschedule(fn)
            pending=getattr(w,"_geom_pending",None)
            if pending:pending.cancel();w._geom_pending=None
            w.completion_enabled=False
            w._rebuild_height_table()
            w.set_grain_level(str(args.grain));w.set_duration(args.period);w.reset();w._rebuild_height_table()
            w.set_sand_grain(.35);w._front_seed=.75
            while w._warm_queue:w._warm_batches_step()
            random.seed(helper.SEED)
            if game._np is not None:game._np.random.seed(helper.SEED)
            w.last_frame,w.last_tick=helper.EPOCH,None
            if args.wrap:
                import short_grain_stream as stream
                half=w._taper["t_in"];pitch=stream.grain_pitch(w._R_inner,game.SAND_MATERIAL_COARSE)
                outlet=2*w._neck_y-w._taper["y_bot"]
                rate=stream.motion_parameters(half,pitch,outlet-(w._lower_y_c-w._R_inner),w._particle_motion_scale)[2]
                boundary=4096/rate if args.wrap=="birth" else 256/3
                args.start=boundary-args.seconds/2
                report["boundary_time"]=boundary
                report["boundary_type"]=args.wrap
            w.toggle()
            logical=0.0
            def advance(target_time):
                nonlocal logical
                draw=w.redraw;w.redraw=lambda:None
                try:
                    while logical+1e-10<target_time:
                        prev=logical;logical=min(target_time,prev+1/120)
                        now[0]=helper.EPOCH+logical;w.tick(logical-prev)
                finally:w.redraw=draw
            fbo=Fbo(size=(args.width,args.height),with_stencilbuffer=True)
            fbo["projection_mat"]=Matrix().view_clip(0,args.width,0,args.height,-1,1,0)
            fbo["modelview_mat"]=Matrix()
            with fbo:ClearColor(*game.hex_rgb(game.BG_COLOR),1);ClearBuffers()
            report.update(version=game.APP_VERSION,renderer=w.flow_renderer,grain=args.grain,period=args.period,
                          sample_rate=args.fps,expected_frames=round(args.seconds*args.fps)+1)
            crop=(0,0,args.width,args.height)
            if args.crop_stream:
                half=w._taper["t_in"]
                outlet=args.height-(2*w._neck_y-w._taper["y_bot"])
                crop=(max(0,int(w._cx-2*half)),max(0,int(outlet-2*half)),
                      min(args.width,int(w._cx+2*half)+1),args.height)
                report["canvas_dimensions"]=[args.width,args.height]
                report["crop_box"]=list(crop)
                report["dimensions"]=[crop[2]-crop[0],crop[3]-crop[1]]
            for i in range(report["expected_frames"]):
                t=args.start+i/args.fps;advance(t);w.redraw()
                hidden=native_hidden_check(Window,Config)
                fbo.add(w.canvas)
                try:fbo.draw();raw=bytes(fbo.pixels)
                finally:fbo.remove(w.canvas)
                im=Image.frombytes("RGBA",(args.width,args.height),raw).transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
                if args.crop_stream:
                    if i==0:im.save(args.output/"overview.png")
                    im=im.crop(crop)
                name="%04d.png"%i;im.save(args.output/name)
                geo=dict(cx=float(w._cx)-crop[0],outlet_y=args.height-(2*w._neck_y-w._taper["y_bot"])-crop[1],
                         surface_y=args.height-w.get_mound_top_y()-crop[1],half_width=w._taper["t_in"],
                         radius=w._R_inner,background_rgb=[c*255 for c in game.hex_rgb(game.GLASS_FILL)])
                report["frames"].append(dict(time=t,file=name,geometry=geo,native_hidden=hidden,
                    sha256=hashlib.sha256((args.output/name).read_bytes()).hexdigest(),elapsed=w.elapsed,
                    draw_state=dict(getattr(w,"_short_flow_state",{}))))
                if i%30==0:save();print("MOTION_FRAME",i,flush=True)
            report["source_files_end"]=source_manifest(args.runtime,helper)
            report["hidden_at_finish"]=native_hidden_check(Window,Config)
            report["completed"]=report["source_files"]==report["source_files_end"]
            report["final_physics"]=helper.physical_state(w,game)
            save()
        finally:Window.close()
    print("MOTION_CAPTURE_DONE",target)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runtime",type=Path,default=ROOT);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--width",type=int,default=540);p.add_argument("--height",type=int,default=924)
    p.add_argument("--start",type=float,default=8);p.add_argument("--seconds",type=float,default=4)
    p.add_argument("--fps",type=int,default=60);p.add_argument("--grain",type=int,default=3)
    p.add_argument("--period",type=float,default=50)
    p.add_argument("--wrap",choices=("birth","shape"))
    p.add_argument("--crop-stream",action="store_true",help="Lossless stream ROI in each original FBO frame; save full first frame too")
    args=p.parse_args();args.runtime=args.runtime.resolve();args.output=args.output.resolve()
    capture(args)
