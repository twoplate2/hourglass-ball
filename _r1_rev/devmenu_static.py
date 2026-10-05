"""A2: 长周期(600s)下场景近似静止, 菜单打开时对比 512² 正式材质 vs 128² 预览材质。"""
import importlib.util, json, os, sys, tempfile
from pathlib import Path
import random
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "r1_devmenu_static"; OUT.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="r1-dm2-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1",
                      KIVY_METRICS_DENSITY="1", KIVY_METRICS_FONTSCALE="1")
    sys.path.insert(0, str(ROOT))
    from kivy.app import App
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
    from PIL import Image
    import main as module
    module.HourglassWidget._make_sound_proxy = lambda *_: None
    module.HourglassWidget._make_completion_sound = lambda *_: None
    module.HourglassApp.on_completed = lambda *_: None
    module.HourglassWidget.load_config = lambda *_: {"duration": 60}
    module.HourglassWidget.save_config = lambda *_: None
    log = []

    class T(App):
        def build(self):
            self.app = module.HourglassApp(); return self.app.build()
        def on_start(self):
            Clock.schedule_once(self.step0, 0.6)
        def grab(self, tag):
            w, h = map(int, Window.size)
            px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
            img = Image.frombytes("RGBA", (w, h), px).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            if img.size != (400, 800):
                img = img.resize((400, 800), Image.Resampling.LANCZOS)
            img.save(OUT / f"{tag}.png")
        def step0(self, _dt):
            r = self.root; r.apply_orientation(); r.do_layout(); r._anchor.do_layout()
            self.app.hourglass.parent.do_layout()
            self.app.hourglass.set_duration(600.0)
            random.seed(23); self.app.hourglass.toggle()
            Clock.schedule_once(self.step1, 2.0)
        def step1(self, _dt):
            self.grab("A_menu_full_512")
            self.app._open_dev_menu()
            Clock.schedule_once(self.step2, 0.4)
        def step2(self, _dt):
            self.grab("B_menu_open_full_512")
            hg = self.app.hourglass
            ok = hg.set_sand_grain(module.SAND_MATERIAL_GRAIN, preview=True)
            log.append(("preview ok", ok, str(hg._preview_material.texture.size)))
            Clock.schedule_once(self.step3, 0.25)
        def step3(self, _dt):
            self.grab("C_menu_open_preview_128")
            self.app.hourglass.set_sand_grain(module.SAND_MATERIAL_GRAIN)
            Clock.schedule_once(self.step4, 0.25)
        def step4(self, _dt):
            self.grab("D_menu_open_full_again")
            self.app._dev_popup.dismiss()
            Clock.schedule_once(self.step5, 0.5)
        def step5(self, _dt):
            self.grab("E_after_close")
            (OUT / "log.json").write_text(json.dumps(log, indent=2, default=str))
            self.stop()
    T().run(); print(log)
