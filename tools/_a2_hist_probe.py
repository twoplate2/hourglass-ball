# -*- coding: utf-8 -*-
"""A2: grain_tone 下 `_stream_grain_idx` 的档位直方图 —— 直接证/伪 "色阶 3→11"."""
import os, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"
from kivy.clock import Clock
from kivy.core.window import Window
import main as m
m.HourglassWidget._make_sound_proxy = lambda *_: None
m.HourglassWidget._make_completion_sound = lambda *_: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 15.0}
m.HourglassWidget.save_config = lambda *_: None

class Probe(m.HourglassApp):
    def on_start(self):
        Window.size = (1602, 2560)
        Clock.schedule_once(self.go, 1.2)
    def go(self, _dt):
        hg = self.hourglass
        hg.set_duration(15.0); hg.size=(1602,2560); hg._rebuild_height_table()
        hg.reset(); hg.toggle()
        for i in range(int(15*165)+5):
            hg.tick(1/165.0)
        pv = hg._pv; n = hg.pn
        xs = np.asarray(pv.nx[:n]); ys = np.asarray(pv.ny[:n]); pw = np.asarray(pv.nwp[:n])
        idx = hg._stream_grain_idx(xs, ys, pw)
        print("HIST grain_tone=%s QUANT=%d  pn=%d" % (m.FLOW_GRAIN_TONE, m.FLOW_TONE_QUANT, n))
        if idx is None:
            print("HIST grain idx 返回 None (材质取不到)")
        else:
            u,c = np.unique(idx, return_counts=True)
            print("HIST grain idx 直方图:", dict(zip(u.tolist(), c.tolist())))
        print("HIST _mat_lum_base=%.1f  _flow_lum=%s" % (hg._mat_lum_base, ["%.0f"%v for v in hg._flow_lum]))
        lum = hg._material_luminance()
        if lum is not None:
            print("HIST 材质亮度场: mean=%.1f std=%.1f min=%.1f max=%.1f" % (lum.mean(), lum.std(), lum.min(), lum.max()))
        # 相位路径(旧)对比: w = int(phase*9/2pi) 上限 8
        wp = (pw * 9 / 6.283185307179586).astype(int)
        u2,c2 = np.unique(np.minimum(wp,8), return_counts=True)
        print("HIST 旧相位档 w 直方图:", dict(zip(u2.tolist(), c2.tolist())))
        self.stop()
Probe().run()
