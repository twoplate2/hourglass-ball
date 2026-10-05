# -*- coding: utf-8 -*-
"""按设备尺度拍一张隐藏菜单 —— 验 ① 标题 ② 贴底 ③ 没空白。"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["KIVY_HOME"] = tempfile.mkdtemp()
import main as m
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
from PIL import Image
for f in ("_make_sound_proxy", "_make_completion_sound"):
    setattr(m.HourglassWidget, f, lambda *_: None)
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 60}
m.HourglassWidget.save_config = lambda *_: None


class P(m.HourglassApp):
    def on_start(self):
        Clock.schedule_once(self.resize, 1)

    def resize(self, _dt):
        ratio = Window.width / Window.system_size[0]
        Window.system_size = (round(1096 / ratio), round(2214 / ratio))
        Clock.schedule_once(self.go, 0.6)

    def go(self, _dt):
        self.hourglass.set_duration(60)
        self.hourglass.elapsed = 0.0
        if self.hourglass.running:
            self.hourglass.toggle()
        self._open_dev_menu()
        Clock.schedule_once(self.grab, 1.2)

    def grab(self, _dt):
        w, h = map(int, Window.size)
        px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
        Image.frombytes("RGBA", (w, h), px).transpose(
            Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB").resize(
            (w // 2, h // 2), Image.Resampling.LANCZOS).save(
            "_shot/devmenu_check.png")
        print("-> _shot/devmenu_check.png")
        self.stop()


P().run()
