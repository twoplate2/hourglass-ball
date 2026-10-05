"""A2: 真实拖动隐藏菜单两个滑块, 抓帧看 128² 预览 vs 512² 正式版的差别与提交时刻。"""
import importlib.util, json, os, sys, tempfile, time as _time
from pathlib import Path
import random
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "r1_devmenu"
OUT.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix="r1-devmenu-") as home:
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
    import main as module
    module.HourglassWidget._make_sound_proxy = lambda *_: None
    module.HourglassWidget._make_completion_sound = lambda *_: None
    module.HourglassApp.on_completed = lambda *_: None
    module.HourglassWidget.load_config = lambda *_: {"duration": 60}
    module.HourglassWidget.save_config = lambda *_: None

    log = []

    class T(App):
        def build(self):
            self.app = module.HourglassApp()
            return self.app.build()

        def on_start(self):
            Clock.schedule_once(self.step0, 0.6)

        def grab(self, tag):
            w, h = map(int, Window.size)
            px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
            img = Image.frombytes("RGBA", (w, h), px).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            if img.size != (400, 800):
                img = img.resize((400, 800), Image.Resampling.LANCZOS)
            img.save(OUT / f"{tag}.png")
            log.append((tag, _time.time()))

        def step0(self, _dt):
            r = self.root
            r.apply_orientation(); r.do_layout(); r._anchor.do_layout()
            self.app.hourglass.parent.do_layout()
            self.app.hourglass.set_duration(5.0)
            random.seed(23)
            self.app.hourglass.toggle()          # 开始, 真实时钟跑
            self.grab("00_running")
            Clock.schedule_once(self.step1, 2.0)

        def step1(self, _dt):
            self.app._open_dev_menu()
            Clock.schedule_once(self.step2, 0.5)

        def step2(self, _dt):
            self.grab("01_menu_open")
            self.sl = self.app._dev_sliders
            log.append(("grain_before", module.SAND_MATERIAL_GRAIN))
            self.sl[0].value = 0.0
            Clock.schedule_once(self.step3, 0.18)

        def step3(self, _dt):
            self.grab("02_drag_grain0_preview")
            hg = self.app.hourglass
            log.append(("preview_is_set", hg._preview_material is not None,
                        getattr(hg._preview_material.texture, "size", None) if hg._preview_material else None))
            self.sl[0].value = 100.0
            Clock.schedule_once(self.step4, 0.18)

        def step4(self, _dt):
            self.grab("03_drag_grain70_preview")
            Clock.schedule_once(self.step5, 0.75)      # 停手 0.35s 后 commit 已发生

        def step5(self, _dt):
            self.grab("04_after_commit_full")
            hg = self.app.hourglass
            log.append(("preview_cleared", hg._preview_material is None))
            log.append(("grain_after_commit", module.SAND_MATERIAL_GRAIN))
            log.append(("cur material size", hg._current_material().texture.size))
            # 直接改玻璃反光滑块
            self.sl[1].value = 100.0
            Clock.schedule_once(self.step6, 0.2)

        def step6(self, _dt):
            self.grab("05_glass_hl_25x")
            log.append(("GLASS_HL_MUL", module.GLASS_HL_MUL))
            # 拖动中途直接点确定 -> 应把未提交的预览烘成正式版
            self.sl[0].value = 30.0
            Clock.schedule_once(self.step7, 0.1)

        def step7(self, _dt):
            self.grab("06_preview_then_close")
            self.app._dev_popup.dismiss()
            Clock.schedule_once(self.step8, 0.5)

        def step8(self, _dt):
            self.grab("07_after_close")
            log.append(("grain_after_close", module.SAND_MATERIAL_GRAIN))
            log.append(("preview_after_close", self.app.hourglass._preview_material))
            (OUT / "log.json").write_text(json.dumps(log, indent=2, default=str))
            self.stop()

    T().run()
    print(json.dumps(log, indent=2, default=str))
