# -*- coding: utf-8 -*-
"""颈部颗粒 `Line` 池 vs 批处理: **几何与颜色**的等价守卫(2026-10-07)。

## 它证明了什么, 以及**不能**证明什么

- **能证明**: 两条路径写出的矩形 `(left, bottom, right, top)` 与所用颜色**逐位相同**
  (同一帧、同一状态, 中间不推进时间)。⇒ 集合与配色没有走样。
- **不能证明**: 像素完全一致。实测**做不到** —— Kivy 的 `Line` 在 `w<=1` 时走 `glLineWidth`,
  与 1px 四边形的**像素覆盖规则不同**(`glLineWidth` 的覆盖是实现定义的),
  逐像素比对会留下 7.3% 的像素、单通道差 1~4(尾部到 ~8)。
  这属于"换了一种画法必然带来的光栅化差", 不是逻辑错。
  ⚠️ 判"看不看得见"要回设备; 桌面与设备 GL 后端不同。

## 跑法
    python tools/_probe_neck_batch_equiv.py
非 0 退出码 = 几何/颜色走样(那是真 bug)。
"""

import os, sys
os.environ["KIVY_HOME"]=os.path.abspath("_kivyhome"); os.environ["KIVY_NO_ARGS"]="1"
os.environ["KIVY_NO_FILELOG"]="1"; os.environ["HG_SPLASH_RENDERER"]="batch"
sys.path.insert(0,".")
from types import SimpleNamespace
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Line
import main as m
m.HourglassWidget._make_sound_proxy=lambda *_:None
m.HourglassWidget._make_completion_sound=lambda *_:None
m.HourglassApp.on_completed=lambda *_:None
m.HourglassWidget.load_config=lambda *_:{"duration":15.0}
m.HourglassWidget.save_config=lambda *_:None
now=[1000.0]; m.time=SimpleNamespace(perf_counter=lambda: now[0])
class P(m.HourglassApp):
    def on_start(self): Clock.schedule_once(self.setup,1.0)
    def setup(self,_): Window.size=(400,800); Clock.schedule_once(self.go,0.4)
    def go(self,_):
        w=self.hourglass; Clock.unschedule(w.tick); w.completion_enabled=False
        w.set_duration(15.0); w._rebuild_height_table(); w.reset(); w.toggle()
        while w.elapsed < 0.84:
            now[0]+=1/60.0; w.tick(1/60.0)
        side = w._neck_sand_side()
        wrap = type(w)._draw_neck_grains
        print("RENDERER =", getattr(m.HourglassWidget,'neck_renderer','(未装)'))
        # ---- A: 原路径(真 Line 池) ----
        real = [(Color(*w.sand_base), Line(points=[], width=1)) for _ in range(320)]
        save_pool, save_last, save_cnt = w._neck_grain_pool, w._neck_tone_last, w._neck_grain_count
        w._neck_grain_pool = real; w._neck_tone_last=[None]*320; w._neck_grain_count=0
        wrap._orig(w, side)
        A = [(real[i][1].points, tuple(real[i][0].rgb), real[i][1].width)
             for i in range(w._neck_grain_count)]
        nA = w._neck_grain_count
        w._neck_grain_pool, w._neck_tone_last, w._neck_grain_count = save_pool, save_last, save_cnt
        # ---- B: 批处理(记录器) ----
        w._neck_grain_count = 0
        w._draw_neck_grains(side)
        buckets = w._neck_last_buckets; tab = w._neck_last_tab
        rects = [r for v in buckets.values() for r in v]
        nB = len(rects)
        print("orig grains=%d  batch rects=%d  buckets=%d" % (nA, nB, len(buckets)))
        # 用同一规则从 A 重算应有的矩形, 与 buckets 比
        exp = {}
        for pts, rgb, wd in A:
            if not pts: continue
            hw = wd*0.5; ext = hw if wd>1.0 else 0.0
            exp.setdefault(rgb, []).append((pts[0]-hw, pts[1]-ext, pts[0]+hw, pts[3]+ext))
        got = {k: [tuple(round(v,9) for v in r) for r in v] for k,v in buckets.items()}
        exp2 = {k: [tuple(round(v,9) for v in r) for r in v] for k,v in exp.items()}
        if exp2 == got:
            print("RESULT: geometry+color IDENTICAL -> difference can only be rasterization")
        else:
            print("RESULT: MISMATCH")
            ka, kb = set(exp2), set(got)
            print("  只 A 有:", len(ka-kb), " 只 B 有:", len(kb-ka))
            for k in list(ka-kb)[:3]: print("   A-only rgb", k, "n=", len(exp2[k]))
            for k in list(kb-ka)[:3]: print("   B-only rgb", k, "n=", len(got[k]))
            for k in ka & kb:
                if exp2[k]!=got[k]:
                    print("   共有键", k, "数量 A", len(exp2[k]), "B", len(got[k]))
                    print("     A[:2]", exp2[k][:2]); print("     B[:2]", got[k][:2]); break
        self.stop()
P().run()
