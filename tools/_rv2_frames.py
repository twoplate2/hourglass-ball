"""Reviewer-2 explicit-time renderer: same scaffolding as inspect_flow, arbitrary targets."""
import argparse, importlib.util, json, math, os, random, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=ROOT/"main.py")
    ap.add_argument("--label", required=True)
    ap.add_argument("--times", required=True, help="comma list of elapsed seconds (period 5)")
    ap.add_argument("--period", type=float, default=5.0)
    ap.add_argument("--grain-preview", type=float, default=None,
                    help="mount a 128px preview material with this grain before capture")
    ap.add_argument("--grain-full", type=float, default=None,
                    help="bake the full-res material with this grain before capture")
    ap.add_argument("--pixels", default="400,800")
    args = ap.parse_args()
    output = ROOT/"benchmark_logs"/("flow_visual_"+args.label); output.mkdir(exist_ok=True)
    targets = [float(x) for x in args.times.split(",")]
    with tempfile.TemporaryDirectory(prefix="rv2f-") as home:
        os.environ["KIVY_HOME"]=home; os.environ["KIVY_NO_ARGS"]="1"; os.environ["KIVY_NO_FILELOG"]="1"
        os.environ["KIVY_METRICS_DENSITY"]="1"; os.environ["KIVY_METRICS_FONTSCALE"]="1"
        sys.path.insert(0,str(ROOT))
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from PIL import Image
        import main  # font registration
        spec = importlib.util.spec_from_file_location("rv2_source", args.source)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": 60}
        module.HourglassWidget.save_config = lambda *_: None
        now=[1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])
        records=[]
        class A(module.HourglassApp):
            def on_start(self): Clock.schedule_once(self.resize,1)
            def resize(self,_dt):
                width,height=map(int,args.pixels.split(","))
                ratio=Window.width/Window.system_size[0]
                Window.system_size=(round(width/ratio),round(height/ratio))
                Clock.schedule_once(self.begin,0.3)
            def begin(self,_dt):
                if args.grain_preview is not None:
                    self.hourglass.set_sand_grain(args.grain_preview, preview=True)
                if args.grain_full is not None:
                    self.hourglass.set_sand_grain(args.grain_full, preview=False)
                r=self.root; r.apply_orientation(); r.do_layout(); r._anchor.do_layout()
                self.hourglass.parent.do_layout()
                Clock.unschedule(self.hourglass.tick)
                self.real=self.hourglass.redraw; self.next(0)
            def next(self,_dt):
                if not targets:
                    (output/"times.json").write_text(json.dumps(records,indent=1))
                    self.stop(); return
                t=targets.pop(0); w=self.hourglass
                if w.duration != args.period or not w.running:
                    if w.duration != args.period: w.set_duration(args.period)
                    w.completion_enabled=False
                    if not w.running: w.toggle()
                    random.seed(23)
                w.redraw=lambda: None
                while w.elapsed+1e-8 < t and w.running:
                    step=min(1/120, t-w.elapsed); now[0]+=step; w.tick(step)
                w.redraw=self.real
                w.redraw()
                records.append(dict(elapsed=w.elapsed, mound_px=w._mound_height_px(),
                                    base_y=w.get_mound_top_y(), particles=w.pn))
                Clock.schedule_once(lambda _dt,tt=t: self.capture(tt), 0.05)
                Clock.schedule_once(self.next, 0.12)
            def capture(self,t):
                width,height=map(int,Window.size)
                px=glReadPixels(0,0,width,height,GL_RGBA,GL_UNSIGNED_BYTE)
                img=Image.frombytes("RGBA",(width,height),px).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                req=tuple(map(int,args.pixels.split(",")))
                if img.size != req: img=img.resize(req, Image.Resampling.LANCZOS)
                img.convert("RGB").save(output/f"period-{args.period}-time-{t:.2f}.png")
                # lower-ball crop for mound work
                x_scale=req[0]/width
                crop=img.convert("RGB").crop((int(40*x_scale),int(430*(req[1]/height)),int(360*x_scale),int(760*(req[1]/height))))
                crop.resize((crop.width*2,crop.height*2), Image.Resampling.NEAREST).save(
                    output/f"mound-{args.period}-time-{t:.2f}.png")
        A().run()
        print("done", output)

main()
