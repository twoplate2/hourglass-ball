# -*- coding: utf-8 -*-
"""PC 端"画面差"的事实核查(专家 2026-10-04 回答里点名要查的三项):
窗口尺寸/DPI 缩放、实际绘制分辨率、MSAA 是否生效。**只打印事实, 不做审美判断。**"""
import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
tmp = tempfile.mkdtemp(prefix="p3-")
os.environ["KIVY_HOME"] = tmp
os.environ["KIVY_NO_ARGS"] = "1"; os.environ["KIVY_NO_FILELOG"] = "1"
sys.path.insert(0, str(ROOT))
from kivy.config import Config
print("kivy config: multisamples =", Config.get("graphics", "multisamples"),
      "| width/height =", Config.get("graphics", "width"), Config.get("graphics", "height"))
from kivy.core.window import Window
from kivy.clock import Clock
from kivy.metrics import dp, sp, Metrics

def probe(_dt):
    print("Window.size(逻辑)      =", tuple(Window.size))
    print("Window.system_size     =", tuple(Window.system_size))
    print("Metrics.density        =", Metrics.density, " (1.0 = 无 DPI 缩放)")
    print("Metrics.dpi            =", getattr(Metrics, "dpi", "?"))
    print("dp(400) =", dp(400), " sp(16) =", sp(16))
    try:
        from kivy.core.gl import glGetIntegerv, GL_SAMPLES, GL_SAMPLE_BUFFERS
        from kivy.graphics.cgl import cgl_get_initialized_backend_name
    except Exception as e:
        print("GL 常量导入失败:", e)
    try:
        from kivy.graphics.cgl import cgl_get_backend_name
        print("GL backend             =", cgl_get_backend_name())
    except Exception as e:
        print("backend?", e)
    try:
        import ctypes
        gl = ctypes.CDLL("opengl32.dll")
        vals = (ctypes.c_int * 1)()
        gl.glGetIntegerv(0x80A9, vals)      # GL_SAMPLES
        n = vals[0]
        gl.glGetIntegerv(0x80A8, vals)      # GL_SAMPLE_BUFFERS
        sb = vals[0]
        print("GL_SAMPLES             =", n, " GL_SAMPLE_BUFFERS =", sb,
              "  ⇒ MSAA", "生效" if (n > 1 and sb == 1) else "**未生效**")
        buf = ctypes.create_string_buffer(256)
        gl.glGetString.restype = ctypes.c_char_p
        print("GL_RENDERER            =", gl.glGetString(0x1F01))
        print("GL_VENDOR              =", gl.glGetString(0x1F00))
    except Exception as e:
        print("GL 查询失败:", type(e).__name__, e)
    App.get_running_app().stop()

from kivy.app import App
class P(App):
    def build(self):
        from kivy.uix.widget import Widget
        Clock.schedule_once(probe, 0.6)
        return Widget()
P().run()
