# -*- coding: utf-8 -*-
"""A2 探针: grain_tone 开/关下的 **活跃桶集合 vs 预热桶集合**、运行期建块数。
(_probe_chunk_need.py 已对 main.py 的 update() 新签名失配 → TypeError, 本探针修掉签名。)"""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"
from kivy.clock import Clock
from kivy.core.window import Window
import main as m
import flow_texture_experiment as fx

m.HourglassWidget._make_sound_proxy = lambda *_: None
m.HourglassWidget._make_completion_sound = lambda *_: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 15.0}
m.HourglassWidget.save_config = lambda *_: None

_KEY = {}
_built = []
_seen = {}
_peak = {}
_oe = fx.TextureFlowBatch._ensure_part
def _pe(self, chunk, count):
    if chunk >= len(self.parts) or self.parts[chunk][3] < count:
        _built.append((_KEY.get(id(self)), chunk, count))
    return _oe(self, chunk, count)
fx.TextureFlowBatch._ensure_part = _pe
_ow = fx.TextureFlowBatch.write_raw
def _pw(self, *a, **k):
    kk = _KEY.get(id(self)); total = a[2]
    if kk is not None and total:
        _seen[kk] = _seen.get(kk, 0) + total
        _peak[kk] = max(_peak.get(kk, 0), total)
    return _ow(self, *a, **k)
fx.TextureFlowBatch.write_raw = _pw
_ou = fx.TextureFlowBatch.update
def _pu(self, *a, **k):
    kk = _KEY.get(id(self)); n = len(a[1])
    if kk is not None and n:
        _seen[kk] = _seen.get(kk, 0) + n
        _peak[kk] = max(_peak.get(kk, 0), n)
    return _ou(self, *a, **k)
fx.TextureFlowBatch.update = _pu

class Probe(m.HourglassApp):
    def on_start(self):
        Window.size = (1602, 2560)
        Clock.schedule_once(self.go, 1.2)
    def go(self, _dt):
        hg = self.hourglass
        D = 15.0
        hg.set_duration(D); hg.size = (1602, 2560); hg._rebuild_height_table()
        live = hg._live_flow_bucket_keys()
        allk = sorted((hg._flow_batches or {}).keys())
        print("A2PROBE grain_tone=%s  QUANT=%d" % (m.FLOW_GRAIN_TONE, m.FLOW_TONE_QUANT))
        print("A2PROBE 总桶=%d  预热集合(live)=%s" % (len(allk), sorted(live)))
        print("A2PROBE 预热队列长度(块数)=%d" % len(getattr(hg, "_warm_queue", ())))
        frames = 0
        while getattr(hg, "_warm_queue", None):
            hg._warm_batches_step(); frames += 1
            if frames > 9999: break
        print("A2PROBE 预热跑完用了 %d 个闲帧; 已建块=%d" %
              (frames, sum(len(b.parts) for b in (hg._flow_batches or {}).values())))
        for k, b in (hg._flow_batches or {}).items(): _KEY[id(b)] = k
        _built.clear(); _seen.clear(); _peak.clear()
        hg.reset(); hg.toggle()
        n = int(D * 165) + 5
        for i in range(n):
            hg.tick(1/165.0); hg.redraw()
        used = sorted(_seen.keys())
        miss = [k for k in used if k[0] not in live]
        print("A2PROBE 运行期建块 %s" % (_built or "无"))
        print("A2PROBE 拿到颗粒的桶(%d 个): %s" % (len(used), used))
        print("A2PROBE 峰值: %s" % sorted(_peak.items(), key=lambda kv: -kv[1])[:6])
        print("A2PROBE **不在预热集合里却拿到颗粒的桶**: %s" % (miss or "无"))
        tones = sorted({k[0] for k in used})
        print("A2PROBE 实际出现的色调档: %s" % tones)
        self.stop()

Probe().run()
