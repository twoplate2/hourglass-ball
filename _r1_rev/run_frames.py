"""R1 review runner: deterministic frames for a given main.py variant.

Usage:
  python _r1_rev/run_frames.py --source main.py --label feath_on \
      --targets "5:0.2,0.5,0.7,0.9,1.0,1.1,1.2,1.5,2.55,4.6;15:1.3,7.5;1:0.2,0.4,0.7"
"""
import argparse, importlib.util, json, math, os, random, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(ROOT / "main.py"))
    ap.add_argument("--label", default="r1")
    ap.add_argument("--pixels", default="400,800")
    ap.add_argument("--targets", default="5:0.2,0.5,0.7,0.9,1.0,1.1,1.2,1.5,2.55,4.6")
    ap.add_argument("--sand", default=None)
    ap.add_argument("--force-preview", type=float, default=None,
                    help="整轮强制用 128² 预览材质(该 grain)")
    ap.add_argument("--material", default=None, help="HG_SAND_MATERIAL 覆写")
    ap.add_argument("--preview-ab", type=float, default=None,
                    help="(deprecated)")
    args = ap.parse_args()
    outdir = ROOT / "benchmark_logs" / ("r1_" + args.label)
    outdir.mkdir(parents=True, exist_ok=True)
    if args.material is not None:
        os.environ["HG_SAND_MATERIAL"] = args.material
    with tempfile.TemporaryDirectory(prefix="r1-visual-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from PIL import Image
        import main  # font registration, same as inspect_flow
        spec = importlib.util.spec_from_file_location("r1_source", Path(args.source))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": 60}
        module.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])

        targets = []
        for chunk in args.targets.split(";"):
            if not chunk.strip():
                continue
            per, times = chunk.split(":")
            for t in times.split(","):
                targets.append((float(per), float(t)))

        records = []

        class VisApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.resize, 1)
            def resize(self, _dt):
                w, h = map(int, args.pixels.split(","))
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(w / ratio), round(h / ratio))
                Clock.schedule_once(self.begin, 0.3)
            def begin(self, _dt):
                if args.force_preview is not None:
                    ok = self.hourglass.set_sand_grain(args.force_preview, preview=True)
                    print("forced preview material:", ok,
                          self.hourglass._current_material() is self.hourglass._preview_material)
                if args.sand:
                    for nm, b, d, l in module.SAND_PRESETS:
                        if nm == args.sand:
                            self.hourglass.set_sand_color(b, d, l)
                self.first_hit = None
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                Clock.unschedule(self.hourglass.tick)
                self.real_draw = self.hourglass.redraw
                self.next_frame(0)
            def next_frame(self, _dt):
                if not targets:
                    (outdir / "measurements.json").write_text(
                        json.dumps(records, indent=2), encoding="utf-8")
                    self.stop()
                    return
                period, target = targets.pop(0)
                widget = self.hourglass
                if widget.duration != period or not widget.running:
                    if widget.duration != period:
                        widget.set_duration(period)
                    widget.completion_enabled = False
                    if not widget.running:
                        widget.toggle()
                    random.seed(23)
                    self.first_hit = None
                widget.redraw = lambda: None
                while widget.elapsed + 1e-8 < target and widget.running:
                    step = min(1 / 120, target - widget.elapsed)
                    now[0] += step
                    widget.tick(step)
                    if widget.flares and self.first_hit is None:
                        self.first_hit = widget.elapsed
                widget.redraw = self.real_draw
                widget.redraw()
                self.duration_btn.text = module._fmt_duration(period)
                self.on_run_state_changed()
                records.append({
                    "period": period, "elapsed": widget.elapsed,
                    "mound_px": widget._mound_height_px(),
                    "mound_top_y": widget.get_mound_top_y(),
                    "particles": len(widget.particles),
                    "splashes": len(widget.splashes),
                    "first_hit_s": self.first_hit,
                    "neck_outlet_y": 2 * widget._neck_y - widget._taper["y_bot"],
                    "R_inner": widget._R_inner, "cx": widget._cx,
                    "cy_low": widget._lower_y_c, "cy_up": widget._upper_y_c,
                    "mound_bot": widget._lower_sand_bot,
                    "upper_bot": widget._upper_sand_bot,
                })
                Clock.schedule_once(
                    lambda _dt, p=period, t=target: self.capture(p, t), 0.05)
                Clock.schedule_once(self.next_frame, 0.12)
            def capture(self, period, elapsed):
                w, h = map(int, Window.size)
                px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (w, h), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM)
                req = tuple(map(int, args.pixels.split(",")))
                if img.size != req:
                    img = img.resize(req, Image.Resampling.LANCZOS)
                img.save(outdir / f"period-{period}-time-{elapsed:.3f}.png")
        VisApp().run()
        print("frames:", outdir)

main()
