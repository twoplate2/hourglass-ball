# -*- coding: utf-8 -*-
"""长周期收尾: 颈部那根"平顶沙塞"到底露头多久、长什么样 (r24-2号 发现7, 中把握)。

他是在 **1.116** 上量的, 报「露头窗口 ≈1.15s, 且随周期线性缩放(100h→115s)」。
但这个项目里没有任何常数是"随周期线性"的排空时间:
    `_neck_fill_time = min(0.25, duration*0.15)`  ⇒ 长周期恒 **0.25s**
    `_natural_flight_time` ≈ **1.10s** (只由几何定, 与周期无关)
⇒ **两者都不是 1.15s, 也都不是线性。** 要么他量的窗口是别的东西, 要么 1.116 的行为与现在不同。

本探针在**当前 HEAD** 上, 用一个长周期(duration=3600)把收尾逐帧抓下来:
  ① 记每帧 `_neck_sand_side()` 的存在性/顶/底 —— 得到真实的可见窗口
  ② 出连环图(filmstrip), 让人**看**那 1 秒里屏幕上是什么
  ③ 负对照: 同时跑一个短周期(20s), 它的窗口应当按 `duration*0.15` 缩到 0.25s 以外的东西上

⚠️ 时间用假 `perf_counter` 驱动, 全程确定性(不依赖真实耗时)。
⚠️ 抓图必须在**渲染之后**的下一帧取, 不能在 redraw() 后立刻 glReadPixels(读的是旧帧缓冲)。

跑法: python tools/_probe_neck_drain.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "drain"
DUR = 3600
WARM_FROM = DUR - 6.0
# ("pre", 目标 elapsed) 完成前; ("post", _done_at 之后的秒数) 排空段
T_CAP = ([("pre", DUR + x / 100.0) for x in (-100, -60, -40, -20, -10, -4)]
         + [("post", x) for x in (0.0, 0.03, 0.06, 0.10, 0.14, 0.18, 0.22,
                                  0.26, 0.32, 0.40, 0.60, 1.00, 1.60, 3.00)])


def main():
    with tempfile.TemporaryDirectory(prefix="neckdrain-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from PIL import Image
        import numpy as np

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": DUR}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        def shot():
            ww, hh = map(int, Window.size)
            px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
            return np.asarray(Image.frombytes("RGBA", (ww, hh), px).transpose(
                Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w.set_duration(DUR)
                w.running = True
                w.elapsed = WARM_FROM
                now[0] = 1000.0
                w.last_frame = w.last_tick = now[0]
                dt = 1.0 / 60.0
                # 预热: 让粒子群达到稳态(飞行时间 ≈1.1s, 跑 5s 足够)
                while w.elapsed < T_CAP[0][1]:
                    now[0] += dt
                    w.tick(dt)
                self.w = w
                self.rows = []
                self.i = 0
                self.next_step = 0.005
                print("")
                print("  duration=%ds  fill_t=%.4fs  flight=%.4fs"
                      % (DUR, w._neck_fill_time, w._natural_flight_time))
                print("")
                print("  elapsed     沙柱点数  顶(y)    底(y)   沙堆顶(y)  上球余沙")
                print("  " + "-" * 66)
                Clock.schedule_once(self.step, 0.25)

            def step(self, _dt):
                if self.i >= len(T_CAP):
                    return self.finish()
                w = self.w
                kind, target = T_CAP[self.i]
                guard = 0
                if kind == "pre":
                    # ⚠️ 完成前: tick 里 elapsed 正常推进
                    while w.elapsed < target and guard < 20000:
                        guard += 1
                        now[0] += self.next_step
                        w.tick(self.next_step)
                else:
                    # ⚠️ 完成后: `tick` 会把 elapsed **钳死在 duration**
                    #    (self.elapsed = self.duration; running=False) ⇒ 绝不能再拿 elapsed
                    #    当判据 —— 第一版就是在 3600.000 上空转到 guard 上限, now[0] 白涨 100 秒,
                    #    排空被瞬间推完, 量出"26→0 一帧"这个**纯探针伪影**。
                    #    排空的真判据是 `time.perf_counter() - _done_at`。
                    if w._done_at is None:
                        w._done_at = now[0]
                    while (now[0] - w._done_at) < target and guard < 20000:
                        guard += 1
                        now[0] += self.next_step
                        w.tick(self.next_step)
                Clock.schedule_once(lambda d: self.take((kind, target)), 0.30)

            def take(self, target):
                w = self.w
                side = w._neck_sand_side()
                top = max((y for _x, y in side), default=None)
                bot = min((y for _x, y in side), default=None)
                uh = w._upper_sand_height_px() if hasattr(w, "_upper_sand_height_px") else -1
                try:
                    mt = w.get_mound_top_y()
                except Exception:
                    mt = float("nan")
                fill_t = w._neck_fill_time
                if w._done_at is None:
                    f = min(1.0, max(0.0, w.elapsed / fill_t))
                    tt = w.elapsed
                else:
                    tt = now[0] - w._done_at
                    f = min(1.0, max(0.0, 1.0 - tt / fill_t))
                self.rows.append((tt, len(side), top, bot, mt, uh, f))
                print("  %-9.3f %-8d %-9s %-8s %-10.1f %-7.1f f=%.3f"
                      % (tt, len(side),
                         "-" if top is None else "%.1f" % top,
                         "-" if bot is None else "%.1f" % bot, mt, uh, f))
                img = shot()
                self.frames = getattr(self, "frames", [])
                self.frames.append((target, img))
                self.i += 1
                Clock.schedule_once(self.step, 0.02)

            def finish(self):
                # 可见窗口: 从"沙柱首次非空且上球已空"到"沙柱变空"
                f = [(e, n) for e, n, _t, _b, _m, _u, _f in self.rows]
                print("")
                print("  序列 (elapsed, 点数): %s"
                      % ", ".join("%.2f:%d" % (e, n) for e, n in f))
                OUT.mkdir(parents=True, exist_ok=True)
                self.montage()
                Clock.schedule_once(lambda d: self.stop(), 0.2)

            def montage(self):
                from PIL import Image, ImageDraw
                w = self.w
                cx = int(w.to_window(w._cx, 0)[0])
                HH = self.frames[0][1].shape[0]

                # ⚠️ **坐标必须转**: `to_window` 给的是**窗口 y(原点左下、y 向上)**,
                #    而截完图 FLIP_TOP_BOTTOM 后的行号是**自上而下** ⇒ 差一个 H-1-y。
                #    上一版直接把窗口 y 当行号用, 裁出来是整幅沙漏(而且没报错)。
                def row(y_kivy):
                    return HH - 1 - int(w.to_window(0, y_kivy)[1])

                # 颈部带: 喇叭口上端 → 出口下方一点
                neck_band = (row(w._taper["in_pts"][0][1]) - 6,
                             row(2 * w._neck_y - w._taper["y_bot"]) + 6)
                assert neck_band[0] < neck_band[1], neck_band
                OUT.mkdir(parents=True, exist_ok=True)

                def strip(sel, band, halfw, zoom, tag, note):
                    crops = []
                    for tgt, im in sel:
                        x0 = max(0, cx - halfw)
                        x1 = min(im.shape[1], cx + halfw)
                        c = Image.fromarray(im).crop((x0, band[0], x1, band[1]))
                        if zoom != 1:
                            c = c.resize((c.width * zoom, c.height * zoom),
                                         Image.NEAREST)
                        crops.append((tgt, c))
                    if not crops:
                        return
                    pad, lab = 6, 22
                    cols = min(5, len(crops))
                    rows = (len(crops) + cols - 1) // cols
                    cw, ch = crops[0][1].size
                    out = Image.new("RGB", (cols * (cw + pad) + pad,
                                            rows * (ch + lab + pad) + pad), (24, 24, 24))
                    d = ImageDraw.Draw(out)
                    for k, (tgt, c) in enumerate(crops):
                        r, cc = divmod(k, cols)
                        x = pad + cc * (cw + pad)
                        y = pad + r * (ch + lab + pad)
                        out.paste(c, (x, y + lab))
                        k2, v2 = tgt
                        txt = ("elapsed %.1f" % v2) if k2 == "pre" else ("+%.2fs" % v2)
                        d.text((x + 3, y + 5), txt, fill=(255, 214, 110))
                    pth = OUT / tag
                    out.save(pth)
                    print("  -> %s  (%dx%d, %d 格)  %s"
                          % (pth, out.width, out.height, len(crops), note))

                drain_sel = [p for p in self.frames
                             if p[0][0] == "post" and p[0][1] <= 1.01]
                late_sel = [p for p in self.frames
                            if p[0][0] == "pre" and p[0][1] >= DUR - 4.01]
                strip(drain_sel, neck_band, 120, 3, "drain_neck_zoom3x.png",
                      "排空段, 颈部 3x 放大")
                ub_band = (row(w._upper_y_c + w._R_inner) - 4,
                           row(2 * w._neck_y - w._taper["y_bot"]) + 40)
                strip(late_sel, ub_band, 150, 2,
                      "last4s_upperball_2x.png", "最后 4 秒, 上球底+颈, 2x")
                # 只盯"上球底那层扁沙" —— 球底往上 60px, 6x
                thin_sel = [p for p in self.frames
                            if p[0][0] == "pre" and p[0][1] >= DUR - 6.01]
                thin_band = (row(w._upper_sand_bot + 26), row(w._upper_sand_bot - 14))
                strip(thin_sel, thin_band, 90, 6,
                      "last6s_thin_layer_6x.png", "上球底那层扁沙, 6x")

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
