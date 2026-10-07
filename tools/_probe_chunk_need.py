# -*- coding: utf-8 -*-
"""**沙流块够不够预热**: 数"运行期现建了几块" + 每个桶的峰值颗数(2026-10-07)。

## 为什么要有它

沙流按 `(色调, 线宽)` 分 24 个桶, 每个桶按需建"块"(容量恒 `CHUNK=512`)。建一块要
**160 KiB 顶点表**(512 槽 × 20 顶点 × 4 float × 4B), 设备实测 **~7.7ms** —— 只能发生在
**没在跑**的闲帧里(`warm()`), 否则就是用户说的"**有个帧必然很低**"。

而"一个桶要几块" = `ceil(桶峰值 / 512)`, **桶峰值 ∝ 粒子的下落路程 ∝ 窗口高度**
⇒ **本机 400×800 的测试窗口永远只要 1~2 块, 复现不出平板上的问题**。
(2026-10-07: 平板 1904×2890 上最热的桶峰值 1525 ⇒ 要 3 块, 而当时只预热 2 块。)

## 用法

    python tools/_probe_chunk_need.py                # 平板 1904x2890 + 本机 400x800
    python tools/_probe_chunk_need.py 1904 2890      # 指定窗口

判据(两条都要看):
 1. **`运行期建块` 必须是"无"** —— 只要不是空, 那一帧的 `图元` 就会多 ~7.7ms;
 2. `死桶里出现的` 必须是空 —— 这条**证伪**"只有 8 个桶拿得到颗粒"的推导
    (见 `main.HourglassWidget._live_flow_bucket_keys`)。它一旦非空, 说明那些桶
    被滤掉之后**颗粒会凭空消失**(不是省了白画的四边形)。

⚠️ 别拿它当性能量具 —— 它只数**结构**(块数/颗数), 不读图也不计时。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

from kivy.clock import Clock                     # noqa: E402
from kivy.core.window import Window              # noqa: E402
import main as m                                 # noqa: E402
import flow_texture_experiment as fx             # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *_: None
m.HourglassWidget._make_completion_sound = lambda *_: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 15.0}
m.HourglassWidget.save_config = lambda *_: None

_KEY = {}
_built = []          # 运行期真正建的块 [(桶, 第几块, 那一刻该桶的颗数)]
_peak = {}           # 桶码 -> 该桶峰值颗数
_seen = set()        # 真正拿到过颗粒的桶

_oe = fx.TextureFlowBatch._ensure_part
def _patched_ensure(self, chunk, count):
    if chunk >= len(self.parts) or self.parts[chunk][3] < count:
        _built.append((_KEY.get(id(self)), chunk, count))
    return _oe(self, chunk, count)
fx.TextureFlowBatch._ensure_part = _patched_ensure

_ow = fx.TextureFlowBatch.write_raw
def _patched_write(self, raw, off, total):
    k = _KEY.get(id(self))
    if total:
        _seen.add(k)
        if total > _peak.get(k, 0):
            _peak[k] = total
    return _ow(self, raw, off, total)
fx.TextureFlowBatch.write_raw = _patched_write

_ou = fx.TextureFlowBatch.update
def _patched_update(self, view, indices, top_limit, motion_scale=1):
    k, n = _KEY.get(id(self)), len(indices)
    if n:
        _seen.add(k)
        if n > _peak.get(k, 0):
            _peak[k] = n
    return _ou(self, view, indices, top_limit, motion_scale)
fx.TextureFlowBatch.update = _patched_update


def run_tier(hg, D, W, H, live):
    global _built, _peak, _seen
    _built, _peak, _seen = [], {}, set()
    _KEY.clear()
    hg.set_duration(D)
    hg.size = (W, H)
    hg._rebuild_height_table()
    for k, b in (hg._flow_batches or {}).items():
        _KEY[id(b)] = k
    frames = 0
    while True:                        # 抽干预热队列 = 基准给的那 0.5s 空闲窗口
        hg._warm_batches_step()
        frames += 1
        if not hg._warm_queue or frames > 9999:
            break
    parts = sum(len(b.parts) for b in (hg._flow_batches or {}).values())
    quads = sum(p[4] for b in (hg._flow_batches or {}).values() for p in b.parts)
    _built.clear()                     # 只记**运行期**建的
    hg.reset()
    hg.toggle()
    for _ in range(min(int(D * 165) + 5, 165 * 25)):
        hg.tick(1 / 165.0)
        hg.redraw()
    hot = max(_peak.items(), key=lambda kv: kv[1]) if _peak else (None, 0)
    dead = {k for k in _seen if k and k[0] not in live}
    print("  %4dx%-5d D=%-5g | 预热%3d 帧, 块%2d / 四边形%5d | 最热桶 %s 峰%5d → 需%d块 | "
          "运行期建块 %s | 死桶里出现的 %s"
          % (W, H, D, frames, parts, quads, hot[0], hot[1], -(-hot[1] // 512),
             _built or "无", sorted(dead) or "无"))


class Probe(m.HourglassApp):
    def on_start(self):
        Window.size = (400, 800)
        Clock.schedule_once(self.go, 1.2)

    def go(self, _dt):
        hg = self.hourglass
        sizes = [(400, 800)]
        if len(sys.argv) > 2:
            sizes = [(int(sys.argv[1]), int(sys.argv[2]))]
        else:
            sizes.append((1904, 2890))
        for (W, H) in sizes:
            hg.set_duration(15.0)
            hg.size = (W, H)
            hg._rebuild_height_table()
            live = hg._live_flow_bucket_keys()
            print("\n窗口 %dx%d: 预热桶集合 = %s (共 %d 个桶, 死桶 %d 个)"
                  % (W, H, sorted(live), len(hg._flow_batches or {}),
                     len(hg._flow_batches or {}) - len(live) * 2 + max(0, len(live) * 2 - len(hg._flow_batches or {}))))
            for D in (1.0, 5.0, 15.0, 60.0, 600.0, 3600.0):
                run_tier(hg, D, W, H, live)
        self.stop()


Probe().run()
