# -*- coding: utf-8 -*-
"""交界处的"两个渲染对不上"能差多少 —— **可标定**的判据。

背景(2026-10-09, 用户报的): 「上面沙漏瓶子沙子的渲染, 和沙流沙柱的渲染, 这2个渲染是
不同的, 所以可以看到界限」。根因是基础色取色走的是**各图元自己的 `tex_coord0`**
(上球矩形铺整张纹理 / 颈部 Quad 带铺 `0.5±t_in/直径` 一小条) ⇒ 同一张噪声在交界处对不上。

判据: 在**交界上下各取一条横带**, 各算三个量 ——
  · 均值 luminance (`mu`)      —— 明暗对不对得上
  · 标准差 (`sd`)              —— 颗粒反差对不对得上
  · 水平自相关半衰长度 (`L`)   —— **颗粒尺度**对不对得上
报 `|Δmu|` / `|Δsd|/sd_上` / `|ΔL|/L_上` 三个相对差。

⚠️ **必须先标定**: `HG_WORLD_UV=0` 是"已知错"(旧行为), `=1` 是"已知对"。
   两者分不开 ⇒ 这个量具是废的, 换判据, 不许拿它下结论。

⚠️ **取样轴必须用 widget 自报的 `_cx`**, 不是 `W//2`。
   (踩过: `export_to_png` 导出的 PNG 只有 356 宽而 `_cx=200`, 按 `W//2` 采样偏轴 22px,
    量出来全是"平滑", 白绕了好几圈。) 探针**回读并断言** PNG 尺寸 == widget 尺寸。
"""
import math
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"
TAG = sys.argv[1] if len(sys.argv) > 1 else "uv"
PERIOD = float(os.environ.get("JP", "50"))
SIZE = tuple(int(v) for v in os.environ.get("JSIZE", "400x800").split("x"))
AT = float(os.environ.get("JAT", "10"))

with tempfile.TemporaryDirectory(prefix="junc-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    now = [1000.0]
    m.time = SimpleNamespace(perf_counter=lambda: now[0])

    class P(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = SIZE
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation()
            self.root.do_layout()
            self.root._anchor.do_layout()
            self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(PERIOD)
            w.reset()
            w._rebuild_height_table()
            w.toggle()
            dt = 1.0 / 60.0
            while w.elapsed < AT - 1e-9:
                now[0] += dt
                w.tick(dt)
            w.running = False
            w.redraw()
            out = ROOT / "benchmark_logs" / "_vid" / "seam"
            out.mkdir(parents=True, exist_ok=True)
            path = out / ("%s_%05.1f.png" % (TAG, AT))
            w.export_to_png(str(path))

            from PIL import Image
            im = Image.open(path).convert("RGB")
            W, H = im.size
            ws, hs = int(round(w.size[0])), int(round(w.size[1]))
            assert abs(W - ws) <= 1 and abs(H - hs) <= 1, (
                "PNG %s != widget %s —— 回读断言失败, 后面的取样坐标全不算数" % (im.size, w.size))
            cx = int(round(w._cx))
            assert 0 <= cx < W, "widget 自报的 _cx=%d 落在 PNG(宽 %d) 之外" % (cx, W)
            inlet = w._taper["in_pts"][0][1]
            up_bot = w._upper_sand_bot
            outlet = 2 * w._neck_y - w._taper["y_bot"]
            found = self.find_tube(im, w._taper["t_in"])
            assert found, "认不出管子/沙柱那一段 —— 判据不成立"
            tube_top, tx0, tx1, tlen = found
            gap = max(10, tlen // 6)
            # png 行号向下增 ⇒ "上"是行号小的一侧
            bands = {"上(球里)": (tube_top - 2 * gap, tube_top - gap),
                     "下(柱里)": (tube_top + gap, tube_top + 2 * gap)}
            cx = (tx0 + tx1) // 2        # **从图里认出来的**轴, 不用 widget 的 _cx
            print("  管内行=[%d,%d] 长 %d 行  轴 x=%d (widget 自报 _cx=%d)"
                  % (tube_top, tube_top + tlen - 1, tlen, cx, int(round(w._cx))))
            print("  band(行): 上=%s  下=%s" % (bands["上(球里)"], bands["下(柱里)"]))

            print("")
            print("  TAG=%s HG_WORLD_UV=%r  周期%.0fs t=%.1fs  widget=%s PNG=%s  _cx=%d"
                  % (TAG, os.environ.get("HG_WORLD_UV", "1"), PERIOD, AT,
                     (ws, hs), im.size, cx))
            print("  入口 y=%.1f  球内底 y=%.1f  出口 y=%.1f  带宽=%.1fpx"
                  % (inlet, up_bot, outlet, gap))
            stats = {}
            for name, (lo, hi) in bands.items():
                stats[name] = self.measure(im, cx, lo, hi)
            a, b = stats["上(球里)"], stats["下(柱里)"]
            dmu = abs(a[0] - b[0])
            dsd = abs(a[1] - b[1]) / max(1e-6, a[1])
            dlen = abs(a[2] - b[2]) / max(1e-6, a[2])
            for name, (mu, sd, ell, n) in stats.items():
                print("   %-10s mu=%7.2f  sd=%6.2f  L=%5.2fpx  (n=%d)" % (name, mu, sd, ell, n))
            print("   ==> 交界差: |dmu|=%.2f   |dsd|/sd=%.3f   |dL|/L=%.3f" % (dmu, dsd, dlen))
            self.stop()

        @staticmethod
        def find_tube(im, half_guess):
            """**不依赖坐标假设**地找出管子/沙柱那几行 —— 按"沙色游程宽度"认。

            踩过的坑: `export_to_png` 导出的 PNG 与 widget 自己的坐标系**不是同一个**
            (实测: 图形中心在 png x≈177.5 而 widget 自报 `_cx=200`; y 整体偏 126 行)。
            所以 band **不能**由 `_cx`/几何量推, 必须从图里认出来。
            """
            W, H = im.size
            px = im.load()

            def is_sand(c):
                return c[0] - c[2] > 40 and c[2] < 215

            rows = []
            for py in range(H):
                xs = [x for x in range(W) if is_sand(px[x, py])]
                if xs:
                    rows.append((py, xs[0], xs[-1], xs[-1] - xs[0] + 1))
            if not rows:
                return None
            narrow = [r for r in rows if r[3] <= 1.6 * 2 * half_guess]
            if not narrow:
                return None
            # 取**最长的一段连续窄行**(管子+自由柱), 它上方的第一个变宽行 = 交界
            best = cur = [narrow[0]]
            for r in narrow[1:]:
                if r[0] == cur[-1][0] + 1:
                    cur.append(r)
                else:
                    if len(cur) > len(best):
                        best = cur
                    cur = [r]
            if len(cur) > len(best):
                best = cur
            top = best[0][0]
            x0 = min(r[1] for r in best); x1 = max(r[2] for r in best)
            return top, x0, x1, len(best)

        @staticmethod
        def measure(im, cx, lo, hi):
            W, H = im.size
            px = im.load()
            xs = list(range(cx - 10, cx + 11))
            ys = [y for y in range(int(lo), int(hi) + 1) if 0 <= y < H]
            lum = []
            for y in ys:
                lum.append([0.299 * px[x, y][0] + 0.587 * px[x, y][1]
                            + 0.114 * px[x, y][2] for x in xs])
            flat = [v for row in lum for v in row]
            n = len(flat)
            mu = sum(flat) / max(1, n)
            var = sum((v - mu) ** 2 for v in flat) / max(1, n)
            sd = math.sqrt(var)
            # 水平自相关(逐行去均值后按 lag 平均), 取降到 0.5 的 lag
            ell = 0.0
            if sd > 1e-9:
                for lag in range(1, 9):
                    num = cnt = 0
                    for row in lum:
                        rm = sum(row) / len(row)
                        for i in range(len(row) - lag):
                            num += (row[i] - rm) * (row[i + lag] - rm)
                            cnt += 1
                    r = (num / cnt) / var if cnt else 0.0
                    if r < 0.5 and ell == 0.0:
                        ell = float(lag)
                        break
                if ell == 0.0:
                    ell = 9.0
            return mu, sd, ell, n

    P().run()
