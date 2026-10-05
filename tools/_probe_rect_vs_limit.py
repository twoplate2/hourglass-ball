# -*- coding: utf-8 -*-
"""沙体矩形顶(`up_draw`)会不会**超过 carve 的上沿**(`limit`)? —— 同一类 bug 的上一层。

2026-10-06 修完"矩形不含 rough"之后, 矩形被抬高到 `upper_height + lift + crest`。
而 carve 只画到 `limit = _upper_sand_bot + 2·Ri + MOUND_CREST_MARGIN`。
若 `up_draw > limit`, 抬出来的那一条**没有被 carve 抠成玻璃** ⇒ 沙体直接露在球顶外面
—— 正是我刚修的那条的镜像。**必须先验它不存在。**

跑法: python tools/_probe_rect_vs_limit.py
"""
import os, sys, tempfile, types
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory(prefix="rectlimit-") as home:
        os.environ["KIVY_HOME"]=home; os.environ["KIVY_NO_ARGS"]="1"
        os.environ["KIVY_NO_FILELOG"]="1"; os.environ["KIVY_METRICS_DENSITY"]="1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 60}
        m.HourglassWidget.save_config = lambda *_: None
        now=[1000.0]; m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        class P(m.HourglassApp):
            def on_start(self): Clock.schedule_once(self.begin, 1.2)
            def begin(self, _dt):
                self.root.apply_orientation(); self.root.do_layout()
                self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
                w = self.hourglass
                # ⚠️ README 的规矩: 「系数 × 参数」要在**两个极端**都验证。
                #    `crest ∝ 2·Ri`(随尺寸缩放) 而 `MOUND_CREST_MARGIN = 2.0` 是死像素
                #    ⇒ 尺寸越大, crest 越大、余量相对越小。所以必须在**两个极端尺寸**各跑一遍。
                SZ = os.environ.get("HG_SIZE")
                if SZ:
                    w.size = tuple(int(v) for v in SZ.split("x"))
                    w._rebuild_height_table()
                print("  [部件尺寸 %dx%d]" % tuple(w.size))
                w.set_duration(60)
                print("")
                print("  2Ri=%.2f  MOUND_CREST_MARGIN=%.2f" % (2*w._R_inner, m.MOUND_CREST_MARGIN))
                print("")
                print("  %-9s %-9s %-9s %-9s %-9s %s" % ("档","余量(lift+crest)","up_draw","limit","差值","判定"))
                print("  " + "-"*62)
                worst = 1e9
                worst_up = -1e9
                worst_lc = -1e9
                for lv in ("6","4","1"):
                    w.set_rough_level(lv)
                    for f in (0.0,0.01,0.02,0.05,0.2,0.5,0.8,0.95,0.99,1.0):
                        w.elapsed = 60*f
                        uh = w._upper_sand_height_px()
                        lift = max(0.0, w._upper_level_for(uh) - uh)
                        crest = w._upper_rough_crest(uh)
                        up = uh + lift + crest
                        limit = 2*w._R_inner + m.MOUND_CREST_MARGIN
                        d = up - limit
                        worst = min(worst, -d)
                        if up > worst_up:
                            worst_up, worst_lc = up, lift + crest
                        if f in (0.02,0.5,0.99):
                            print("  %-9s %-9.2f %-9.2f %-9.2f %+9.2f %s"
                                  % (lv, lift+crest, up, limit, d, "OK" if d<=0 else "**超了**"))
                print("")
                print("  全程最小余量 = %.2f px  ⇒ %s"
                      % (worst, "**矩形没有捅穿 carve**" if worst>=0 else "**有捅穿, 必须处理**"))
                print("  2Ri=%.1f  MOUND_CREST_MARGIN=%.1f | 最高 up_draw=%.1f "
                      "(其中 lift+crest=%.2f) ⇒ 离上沿还有 %.1f px"
                      % (2*w._R_inner, m.MOUND_CREST_MARGIN, worst_up, worst_lc,
                         (2*w._R_inner + m.MOUND_CREST_MARGIN) - worst_up))
                Clock.schedule_once(lambda d: self.stop(), 0.2)
        P().run()
    return 0

if __name__ == "__main__":
    sys.exit(main())
