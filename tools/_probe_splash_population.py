# -*- coding: utf-8 -*-
"""坡面飞溅到底是**没生成**还是**生成了没画出来**。

r19-1号 2026-10-06 用「高于本地沙面实心边缘的沙粒」当尺子(带**标定**: 拿完成尘埃
当"已知可见"的对照 = 峰值 942px/80 列), 量出坡面(|x-540|>80, 800+px 宽)只有
**15~44 px**、集中在 8~24 列上 —— 比尘埃少 **20~30 倍**。
而用户为这件事点过两次名, 代码也已经改过三轮(1.103 铺开 / 1.104 压平 / 1.109 抬高)。

本探针**不猜**, 直接把 `splashes` 这个列表本身拆开看:
  ① 稳态时**在途**多少颗? 其中多少颗在坡面上(|x-cx|>80)?
  ② 它们的 y 相对**当地沙面**是高是低(低于沙面 = 画不出来);
  ③ 出生多久后被剔除(寿命);
  ④ 生成速率实际是多少(理论 = SPLASH_BG_RATE)。

跑法: python tools/_probe_splash_population.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DURATION = 60.0
WARM = 8.0          # 先跑到稳态(前段在途数量还没涨起来)


def main():
    with tempfile.TemporaryDirectory(prefix="splashpop-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        from kivy.core.window import Window

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": int(DURATION)}
        m.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        m.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w.set_duration(int(DURATION))
                if not w.running:
                    w.toggle()
                w._done_at = None
                # 排到稳态: 直接推进 elapsed, 每一步喂 dt
                t = 0.0
                dt = 1.0 / 60.0
                spawned_at = {}
                self.age_sum = 0.0
                self.ages = []
                self.samples = []
                while t < WARM:
                    now[0] += dt
                    before = set(id(s) for s in w.splashes)
                    w.tick(dt)
                    after = set(id(s) for s in w.splashes)
                    for sid in after - before:
                        spawned_at[sid] = t
                    for sid in before - after:
                        if sid in spawned_at:
                            self.ages.append(t - spawned_at.pop(sid))
                    t += dt
                # 稳态: 再跑 2 秒, 每 10 帧采一次样
                for i in range(120):
                    now[0] += dt
                    w.tick(dt)
                    if i % 10 == 0:
                        self.samples.append(self.snap(w))
                    t += dt
                report(self.samples, self.ages, w)
                # ---- 决定性的一步: A/B 渲染 —— 同状态, 一次带飞溅、一次清空 ----
                # 两帧之差 = 飞溅**实际画出来**的像素。不需要任何"沙面在哪"的模型。
                # ⚠️ **必须让事件循环真的渲一帧再读**: `redraw()` 只改指令树,
                #    紧接着 `glReadPixels` 读到的是**上一帧的帧缓冲** —— 第一版就是这么
                #    写出"两帧差 0 像素"这个假结论的(A 和 B 其实是同一张图)。
                #    所以先停 tick 冻住物理, 再 A/改/B 各**调度一次**。
                w.running = False
                w.redraw()
                Clock.schedule_once(lambda d: self.grab_a(w), 0.35)

            def grab_a(self, w):
                """A = 带飞溅的那一帧(已由事件循环真渲染过)。"""
                self._a = self.shot(w)
                self._kept = w.splashes
                w.splashes = []
                w.redraw()                       # 改指令树
                Clock.schedule_once(lambda d: self.grab_b(w), 0.35)   # 再等真渲染一帧

            def grab_b(self, w):
                """B = 清掉飞溅那一帧。差分即飞溅的像素。"""
                import numpy as np
                b = self.shot(w)
                a = self._a
                w.splashes = self._kept
                w.redraw()
                d = np.abs(a - b).max(axis=2)
                nz = d > 6
                cxw = w.to_window(w._cx, 0)[0]
                ww = a.shape[1]
                print("")
                print("=== 画出来的沙面 vs 求解器的沙面 ===")
                print("  |dx|   求解器(窗口y)   画出来的(窗口y)   差(正=画得更高)")
                hh = a.shape[0]
                # ⚠️ **行序 vs 窗口 y**: `glReadPixels` 翻转过之后 **row 0 = 屏幕最上面**,
                #    而 `to_window` 给的是**窗口 y(从下往上)**。第一版直接拿窗口 y 当行号索引,
                #    于是"画出来的沙面"那几列读到的其实是**上球** —— 整列作废。
                #    换算: row = hh - 1 - window_y。
                base = np.array(w.sand_base) * 255.0
                light = np.array(w.sand_light) * 255.0
                lo_, hi_ = np.minimum(base, light), np.maximum(base, light)
                sand = ((a >= lo_.reshape(1, 1, 3) - 30).all(axis=2)
                        & (a <= hi_.reshape(1, 1, 3) + 30).all(axis=2))
                # ⚠️ 沙色包络罩不住沙堆(实测: 整个下球扫不到 5 连沙色)。
                #    改用**不依赖沙色**的判据: 空玻璃色是已知常量, 从泡顶往下扫,
                #    第一个**不是空玻璃色**的像素就是画出来的沙面。B 图(已清飞溅)上量。
                import main as _m
                glass = np.array([int(_m.GLASS_FILL[i:i + 2], 16) for i in (1, 3, 5)])
                b_img = b
                not_glass = (np.abs(b_img - glass.reshape(1, 1, 3)).max(axis=2) > 12)
                cxw = w.to_window(w._cx, 0)[0]
                top_w = w.to_window(w._cx, w._lower_y_c + w._R_inner)[1]
                bot_w = w.to_window(w._cx, w._lower_y_c - w._R_inner)[1]
                r_lo = max(0, hh - 1 - int(top_w))      # 下球**泡顶**所在行
                r_hi = min(hh - 1, hh - 1 - int(bot_w))  # 泡底所在行
                for dxq in (0, 40, 80, 120, 130):
                    xo = int(round(cxw + dxq))
                    col = not_glass[:, xo]
                    drawn = None
                    # ⚠️ 改成**从泡底往上走**, 走到第一个"空玻璃色"为止。
                    #    前面两种写法(从泡顶往下扫)都会被上面那圈东西挡住:
                    #    先是玻璃描边, 跳过之后又在窗口 y≈345 撞上颈部/喇叭口的几何 ——
                    #    而"沙面**下面**全是沙、上面才是空玻璃"这条从下往上永远成立。
                    r0 = min(r_hi - 10, hh - 1)
                    r = r0
                    while r > r_lo and col[r]:
                        r -= 1
                    if r > r_lo and r < r0:
                        drawn = hh - 1 - (r + 1)         # 换回窗口 y
                    solver = w.to_window(w._cx, w._lower_sand_bot
                                         + w._mound_contact_h(dxq))[1]
                    print("  %4d   %10.1f   %13s   %6s"
                          % (dxq, solver, drawn if drawn is not None else "—",
                             ("%+.1f" % (drawn - solver)) if drawn is not None else "—"))
                print("在途飞溅 %d 颗; 两帧差 >6 级的像素 **%d**" % (len(self._kept), int(nz.sum())))
                print("  %-14s %8s %8s" % ("|x-cx| 箱", "像素数", "最大差"))
                xs = np.arange(ww)
                for lo in (0, 80, 160, 240):
                    sel = (np.abs(xs - cxw) >= lo) & (np.abs(xs - cxw) < lo + 80)
                    sub = d[:, sel]
                    print("  %-14s %8d %8d" % ("%d~%d" % (lo, lo + 79),
                                               int(nz[:, sel].sum()),
                                               int(sub.max()) if sub.size else 0))
                ys, xs2 = np.nonzero(nz)
                if ys.size:
                    print("  差分包围盒: x %d~%d  y %d~%d (窗口 y)"
                          % (xs2.min(), xs2.max(), ys.min(), ys.max()))
                # ---- 按 **r19-1号 那把尺子** 重算: 只数**高出画出来的沙面**的像素 ----
                # 他量到坡面只有 15~44px, 而我上面数到外半 352~391。差别只可能在判据:
                # 我数的是"像素变了"(含画在沙上、沙对沙看不出的), 他只数"高出沙面"的。
                surf_row = {}
                for xo in range(ww):
                    colx = not_glass[:, xo]
                    r0 = min(r_hi - 10, hh - 1)
                    r = r0
                    while r > r_lo and colx[r]:
                        r -= 1
                    surf_row[xo] = (r + 1) if (r > r_lo and r < r0) else None
                above = {}
                for y, x in zip(ys, xs2):
                    sr = surf_row.get(int(x))
                    if sr is None:
                        continue
                    if y < sr:                      # row 更小 = 屏幕上更高 = 高出沙面
                        b = min(int(abs(x - cxw) // 80) * 80, 240)
                        above[b] = above.get(b, 0) + 1
                print("  **按他的尺子**(只数高出画出来的沙面的):")
                for b in sorted(above):
                    print("    |x-cx| %3d~%-3d : %5d px" % (b, b + 79, above[b]))
                print("    合计 **%d px**   (坡面 |x-cx|>=80: **%d px**)"
                      % (sum(above.values()),
                         sum(v for k, v in above.items() if k >= 80)))
                print("  A/B 两帧整体差 >6 的像素: %d (若这个数也是 0, 说明读的还是同一帧)"
                      % int((np.abs(a - b).max(axis=2) > 6).sum()))
                # ---- 密度三档并排(给用户看, 不是给自己下结论) ----
                # 结论指向的是"总量"这条轴, 所以这里只动**数量**, 别的(位置/初速/分布)全不动。
                self._cxw = w.to_window(w._cx, 0)[0]
                self._panels = [(u"现状（%.0f 颗）" % len(w.splashes), a)]
                self._crop = (max(0, r_lo - 24), min(hh, r_hi + 24))
                Clock.schedule_once(lambda d: self.density(w, 2), 0.35)

            def density(self, w, mult):
                """再补一批背景飞溅到 `mult` 倍, 渲一帧, 收面板。"""
                import numpy as np
                target = int(len(w.splashes) / (mult - 1)) if mult > 2 else len(w.splashes)
                per = 24                     # `_spawn_bg_splashes` 单次上限就是 24
                need = max(0, target * (mult - 1) if mult > 2 else target)
                for _ in range(int(need // per) + 1):
                    w._spawn_bg_splashes(0.05)
                w.redraw()
                Clock.schedule_once(lambda d: self.take(w, mult), 0.35)

            def take(self, w, mult):
                import numpy as np
                img = self.shot(w)
                self._panels.append((u"×%d（%.0f 颗）" % (mult, len(w.splashes)), img))
                if mult >= 4:
                    return self.montage()
                Clock.schedule_once(lambda d: self.density(w, 4), 0.2)

            def montage(self):
                from PIL import Image, ImageDraw
                r0, r1 = self._crop
                # ⚠️ 第一版只出了**整球**的并排 —— 三档几乎看不出差别, 因为飞溅是
                #    1~2px 的小点, 缩到整球尺度就没了。**给一个判不了的证据等于没给。**
                #    所以出两版: 整球看整体, 坡面**放大 3 倍**看颗粒。
                imgs = [(t, Image.fromarray(im.astype("uint8"))) for t, im in self._panels]
                cxw = self._cxw
                zx0, zx1 = int(cxw - 175), int(cxw - 5)          # 左侧坡面
                zr0, zr1 = max(0, r0 - 10), min(imgs[0][1].height, r1 + 10)
                for fname, mk in (
                        ("density_3way.png", lambda im: im.crop((0, r0, im.width, r1))),
                        ("density_3way_zoom3x.png",
                         lambda im: im.crop((zx0, zr0, zx1, zr1)).resize(
                             ((zx1 - zx0) * 3, (zr1 - zr0) * 3), Image.NEAREST))):
                    crops = [(t, mk(im)) for t, im in imgs]
                    pad, lab = 8, 26
                    wpx = crops[0][1].width + pad * 2
                    hpx = sum(im.height + lab for _t, im in crops) + pad * (len(crops) + 1)
                    out = Image.new("RGB", (wpx, hpx), (26, 26, 26))
                    d = ImageDraw.Draw(out)
                    y = pad
                    for t, im in crops:
                        out.paste(im, (pad, y + lab))
                        d.text((pad + 4, y + 6), t, fill=(255, 214, 110))
                        y += im.height + lab
                    p = (ROOT / "_shot" / "splashpop") / fname
                    out.save(p)
                    print("  -> %s  (%dx%d)" % (p, out.width, out.height))
                print("")
                print("=== 密度三档并排(只动数量, 位置/初速/分布全不动) ===")
                for t, _im in imgs:
                    print("   - %s" % t)
                Clock.schedule_once(lambda dt_: self.stop(), 0.2)

            @staticmethod
            def shot(w):
                import numpy as np
                from kivy.core.window import Window
                from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
                from PIL import Image
                OUT = ROOT / "_shot" / "splashpop"
                OUT.mkdir(parents=True, exist_ok=True)
                ww, hh = map(int, Window.size)
                px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
                return np.asarray(Image.frombytes("RGBA", (ww, hh), px).transpose(
                    Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"), np.int16)

            @staticmethod
            def snap(w):
                cx, Ri = w._cx, w._R_inner
                airborne = []
                for s in w.splashes:
                    x, y = s.get("x", 0.0), s.get("y", 0.0)
                    h = w._mound_contact_h(x - cx)
                    airborne.append((x - cx, y, w._lower_sand_bot + h, s.get("vy", 0.0)))
                return airborne

        P().run()
    return 0


def report(samples, ages, w):
    print("\n=== 坡面飞溅: 在途种群 ===")
    print("R_inner = %.1f   上下球截面 cx = %.1f" % (w._R_inner, w._cx))
    tot = [len(s) for s in samples]
    print("在途总数(6 次采样): %s   中位 %d" % (tot, sorted(tot)[len(tot) // 2]))
    if ages:
        ages.sort()
        print("实测寿命(被剔除时): n=%d  中位 %.3fs  p90 %.3fs  max %.3fs"
              % (len(ages), ages[len(ages) // 2],
                 ages[int(len(ages) * 0.9)], ages[-1]))
    print("\n--- 按横向位置分箱(白框 = r19-1号 数的'坡面' |x-cx|>80) ---")
    head = "%-14s %7s %9s %9s %9s" % ("箱(|x-cx|)", "颗数", "在沙面以上", "在沙面以下", "平均离地")
    print(head)
    print("-" * len(head))
    edges = [(0, 80), (80, 160), (160, 240), (240, 320), (320, 10000)]
    for lo, hi in edges:
        n = above = below = 0
        gap = 0.0
        for s in samples:
            for dx, y, surf, _vy in s:
                if lo <= abs(dx) < hi:
                    n += 1
                    if y > surf:
                        above += 1
                        gap += (y - surf)
                    else:
                        below += 1
        print("%-14s %7d %9s %9s %9s"
              % ("%d~%s" % (lo, hi if hi < 9999 else "∞"), n,
                 "%d (%.0f%%)" % (above, 100.0 * above / n if n else 0),
                 "%d (%.0f%%)" % (below, 100.0 * below / n if n else 0),
                 "%.1fpx" % (gap / above) if above else "-"))
    print("\n⇒ 若「在沙面以下」占比很高, 说明它们一出生就在沙体里 ⇒ 画不出来(被沙盖住/被剔除)。")
    print("   若「在途总数」本身很小, 那是**生成**的问题, 不是渲染的问题。")


if __name__ == "__main__":
    sys.exit(main())
