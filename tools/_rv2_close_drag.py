"""Reviewer-2: close the dev menu MID-DRAG (< 0.35s) and check nothing is lost."""
from pathlib import Path
import json, os, sys, tempfile
ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="rv2cd-") as home:
    os.environ["KIVY_HOME"]=home; os.environ["KIVY_NO_ARGS"]="1"; os.environ["KIVY_NO_FILELOG"]="1"
    os.environ["KIVY_METRICS_DENSITY"]="1"; os.environ["KIVY_METRICS_FONTSCALE"]="1"
    sys.path.insert(0,str(ROOT))
    from kivy.app import App
    from kivy.clock import Clock
    from kivy.core.window import Window
    import main as module
    module.HourglassWidget._make_sound_proxy = lambda *_: None
    module.HourglassWidget._make_completion_sound = lambda *_: None
    module.HourglassApp.on_completed = lambda *_: None
    module.config_path = lambda: str(Path(home)/"hourglass_config.json")
    res=[]
    def check(n,ok,d=""): res.append((n,ok,d)); print("CHECK %-40s %s %s"%(n,"PASS" if ok else "**FAIL**",d))
    class A(module.HourglassApp):
        def on_start(self): Clock.schedule_once(self.go,1.0)
        def go(self,_dt):
            Window.size=(400,800); Clock.schedule_once(self.begin_run,0.4)
        def begin_run(self,_dt):
            w=self.hourglass; w.set_duration(5.0); w.toggle()
            self._open_dev_menu(); Clock.schedule_once(self.drag,0.4)
        def drag(self,_dt):
            sliders=getattr(self,"_dev_sliders",None)
            check("two sliders", bool(sliders) and len(sliders)==2)
            self.sand_slider, self.glass_slider = sliders
            self.sand_slider.value = 88.0          # -> grain 0.616
            self.glass_slider.value = 60.0         # -> x1.5
            # close IMMEDIATELY (well inside the 0.35s commit window)
            Clock.schedule_once(self.close_now, 0.05)
        def close_now(self,_dt):
            w=self.hourglass
            check("preview still mounted at close time", w._preview_material is not None)
            btn=None
            for child in self._dev_popup.content.walk():
                if getattr(child,"text","")=="确定": btn=child
            check("found 确定 button", btn is not None)
            btn.dispatch("on_press")
            Clock.schedule_once(self.verify,0.15)
        def verify(self,_dt):
            w=self.hourglass
            check("popup closed", self._dev_popup is None)
            check("preview slot cleared", w._preview_material is None)
            ok = w._sand_material is not None and tuple(w._sand_material.texture.size)==(512,512)
            check("full 512 material baked at close", ok, str(tuple(w._sand_material.texture.size)) if w._sand_material else "-")
            check("grain global = 0.616", abs(module.SAND_MATERIAL_GRAIN-0.616)<0.005, "%.4f"%module.SAND_MATERIAL_GRAIN)
            try: cfg=json.load(open(module.config_path(),encoding="utf-8"))
            except Exception as e: cfg={}; print("cfg read fail",e)
            check("config grain persisted", abs(cfg.get("sand_grain",-1)-0.616)<0.005, str(cfg.get("sand_grain")))
            check("config glass persisted", abs(cfg.get("glass_hl",-1)-1.5)<1e-6, str(cfg.get("glass_hl")))
            check("materials rect bound to 512", w._sand_chords[0][1].texture is w._sand_material.texture)
            # is the node bookkeeping sane? (drag with no future timer)
            bad=[r for r in res if not r[1]]
            print("CHECK ==== %d/%d"%(len(res)-len(bad),len(res)))
            self.stop()
    A().run()
