# -*- coding: utf-8 -*-
"""跑起来到中途, 抓整窗 —— 看**下球沙堆那一片**的飞溅。"""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
sys.argv = [sys.argv[0], "_m"]
import main as m
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
from PIL import Image
import numpy as np
m.HourglassWidget._make_sound_proxy = lambda *_: None
m.HourglassWidget._make_completion_sound = lambda *_: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 50}
m.HourglassWidget.save_config = lambda *_: None
TAG = os.environ.get("HG_TAG", "x")
OUT = ROOT / "_shot" / "mound"; OUT.mkdir(parents=True, exist_ok=True)
res = {}
class P(m.HourglassApp):
    def on_start(self):
        Window.size = (400, 800)
        Clock.schedule_once(self.s1, 1.4)
    def s1(self, _dt):
        self.hourglass.duration = float(os.environ.get("HG_DUR", "50"))
        self.hourglass._rebuild_height_table()
        self.hourglass.toggle()               # 开始
        Clock.schedule_once(self.s2, float(os.environ.get("HG_AT", "6.0")))
    def s2(self, _dt):
        w, h = int(Window.width), int(Window.height)
        px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
        img = np.asarray(Image.frombytes("RGBA", (w, h), px)
                         .transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))
        Image.fromarray(img).save(OUT / ("%s.png" % TAG))
        res["ok"] = img.shape
        Clock.schedule_once(lambda dt: self.stop(), 0.2)
P().run()
print("  抓到", res.get("ok"), "->", OUT / ("%s.png" % TAG))
