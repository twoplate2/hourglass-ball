"""Reviewer-2: dump the drawn mound profile (carve quads) at the exact steady targets."""
import os, sys, json, tempfile
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="rv2geom-") as home:
    os.environ["KIVY_HOME"]=home; os.environ["KIVY_NO_ARGS"]="1"; os.environ["KIVY_NO_FILELOG"]="1"
    os.environ["KIVY_METRICS_DENSITY"]="1"; os.environ["KIVY_METRICS_FONTSCALE"]="1"
    sys.path.insert(0,str(ROOT))
    from kivy.app import App
    from kivy.clock import Clock
    from kivy.core.window import Window
    import main
    module = main
    module.HourglassWidget._make_sound_proxy = lambda *_: None
    module.HourglassWidget._make_completion_sound = lambda *_: None
    module.HourglassApp.on_completed = lambda *_: None
    module.HourglassWidget.load_config = lambda *_: {"duration": 60}
    module.HourglassWidget.save_config = lambda *_: None
    import random
    now=[1000.0]
    module.time = SimpleNamespace(perf_counter=lambda: now[0])
    targets = [round(0.5+4.1*i/15,4) for i in range(16)]
    out=[]
    class A(module.HourglassApp):
        def on_start(self): Clock.schedule_once(self.resize,1)
        def resize(self,_dt):
            Window.system_size=(400,800); Clock.schedule_once(self.begin,0.3)
        def begin(self,_dt):
            r=self.root; r.apply_orientation(); r.do_layout(); r._anchor.do_layout()
            self.hourglass.parent.do_layout()
            Clock.unschedule(self.hourglass.tick)
            self.real=self.hourglass.redraw
            self.hourglass.redraw=lambda: None
            self.next(0)
        def next(self,_dt):
            if not targets:
                Path(ROOT/"benchmark_logs"/"_rv2_geom.json").write_text(json.dumps(out,indent=1))
                self.stop(); return
            t=targets.pop(0); w=self.hourglass
            if not w.running:
                w.set_duration(5.0); w.completion_enabled=False; w.toggle(); random.seed(23)
            while w.elapsed+1e-8 < t and w.running:
                step=min(1/120, t-w.elapsed); now[0]+=step; w.tick(step)
            w.redraw=self.real
            w.redraw()
            carve=[list(q.points) for q in w._mound_carve]
            info=dict(elapsed=w.elapsed, mound_px=w._mound_height_px(),
                      base_y=w.get_mound_top_y(), lower_bot=w._lower_sand_bot,
                      Ri=w._R_inner, neck_y=w._neck_y,
                      eff=w._effective_fallen(), delay=w._fall_delay,
                      fill=w._neck_fill_time,
                      rect_mound_h=w._sand_chords[1][1].size[1],
                      carve=carve)
            out.append(info)
            Clock.schedule_once(self.next,0.02)
    A().run()
