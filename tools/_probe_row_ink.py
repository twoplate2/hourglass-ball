# -*- coding: utf-8 -*-
"""**每一行的墨迹在哪** —— 截图量出文字/图标的真实上下边界, 好判"还能往上挪多少"(2026-10-07)。

`_probe_vertical_budget.py` 给的是"每行**占**多少 px", 但那只回答了一半:
**行框里的空白在上还是在下, 取决于文字怎么对齐** —— 若墨迹本来就贴着行框底部,
那么缩行框就是"把文字整体上移 + 画布起点上移"(用户要的效果 ✓);
若墨迹居中, 缩行框只会两边各收一半。

⇒ 本探针找**每一带的非背景像素**的 y 范围, 直接量出来。

跑法: python tools/_probe_row_ink.py [输出前缀]
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

TABLET_W, TABLET_H = 1904, 2890
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

from kivy.clock import Clock                     # noqa: E402
from kivy.core.window import Window              # noqa: E402
from kivy.metrics import dp                      # noqa: E402
import main as m                                 # noqa: E402

m.HourglassWidget._make_sound_proxy = lambda *a, **kw: None
m.HourglassWidget._make_completion_sound = lambda *a, **kw: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 50.0}
m.HourglassWidget.save_config = lambda *_: None

TAG = sys.argv[1] if len(sys.argv) > 1 else "rowink"
# 标定用: `--label-dp=NN` 把倒计时行框改成 NN 再量 —— 用来证明"判据真的会红"
# (拿一个**已知会压到色块行**的值跑一遍, 看笔画顶部是否真的越过色块行底)。
LABEL_DP = next((float(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--label-dp=")), None)


class Probe(m.HourglassApp):
    def on_start(self):
        Window.size = (TABLET_W, TABLET_H)
        Clock.schedule_once(self.check, 1.2)

    def check(self, _dt):
        if LABEL_DP is not None:
            self.time_label.height = dp(LABEL_DP)
            # ⚠️ **改完必须等布局跑过一帧再量** —— 第一版在这里直接截, 量到的是
            #    "行框已改、画布未重排"的**混合态**, 于是负对照(14dp)反而报"通过",
            #    把一个错误模型(以为文字贴行框底边)坐实了。等一帧全是新的了。
            Clock.schedule_once(self.measure, 0.3)
            return
        self.measure(None)

    def measure(self, _dt):
        hg = self.hourglass
        top = self.color_btns[0][1].parent
        bot = self.duration_btn.parent
        # Kivy 的窗口坐标 y 向上; 截图的行号 y 向下 ⇒ 换算一下再报
        def band(name, widget):
            y0 = int(round(TABLET_H - (widget.y + widget.height)))
            y1 = int(round(TABLET_H - widget.y))
            print("     %-8s 行框 y[%4d,%4d] 高 %5.1f" % (name, y0, y1, widget.height))
            return y0, y1
        print("")
        print("  == 行框(截图坐标, y 向下) ==")
        print("     倒计时 文字纹理 %.1f x %.1f px (行框 %.1f; 纹理比行框高就会**溢出到色块行上**)"
              % (self.time_label.texture_size[0], self.time_label.texture_size[1],
                 self.time_label.height))
        bands = [("色块", band("色块", top)), ("倒计时", band("倒计时", self.time_label)),
                 ("画布", band("画布", hg)), ("底栏", band("底栏", bot))]
        # ⚠️ `name` **必须带扩展名** —— Kivy 按最后一个 "." 剁开再拼序号; 裸名存不出来
        #    (踩过: 只给了 "rowink_0", 结果一个文件都没有)。存出来是 `rowink_0_0001.png`。
        Window.screenshot(name="%s_0.png" % TAG)
        Clock.schedule_once(lambda _d: self.analyze(bands), 0.4)

    def analyze(self, bands):
        from PIL import Image
        files = sorted(Path(".").glob("%s_0*.png" % TAG))
        if not files:
            print("     !! 没截到图"); self.stop(); return
        path = files[-1]
        img = Image.open(path).convert("RGB")
        px = img.load()
        w, h = img.size
        bg = px[3, 3]                     # 左上角 = 根 padding, 恒为背景色
        print("     截图 %s  %dx%d  背景 %s" % (path.name, w, h, bg))

        def ink(y0, y1, thr=18):
            rows = []
            for y in range(max(0, y0), min(h, y1)):
                n = sum(1 for x in range(0, w, 3)
                        if max(abs(px[x, y][i] - bg[i]) for i in range(3)) > thr)
                rows.append((y, n))
            hit = [y for y, n in rows if n > 2]
            return (hit[0], hit[-1]) if hit else None

        print("")
        print("  == 每带的墨迹范围(与行框比 ⇒ 空白在上还是在下) ==")
        got = {}
        for name, (y0, y1) in bands:
            r = ink(y0, y1)
            got[name] = (y0, y1, r)
            if r is None:
                print("     %-8s 行框[%4d,%4d]  这一带没测到墨迹" % (name, y0, y1))
                continue
            print("     %-8s 行框[%4d,%4d] 墨迹[%4d,%4d]  ⇒ 上留白 %4dpx  下留白 %4dpx"
                  % (name, y0, y1, r[0], r[1], r[0] - y0, y1 - r[1]))
        # ---- 判据: 倒计时那行**有没有把字切掉** ----
        # 判据写法(自标定, 无魔法数): 把行框**临时开到 dp(40)**(已知不会切)量一遍笔画高,
        # 当作参照; 当前笔画高比参照矮 ⇒ 被切了 ⇒ 红。
        # 🔴 为什么换掉原来那条("笔画顶 vs 色块行底"): **它永远红不了** —— 行框变小时纹理
        #    被裁, 笔画顶被顶在行框顶上, 而行框顶恒在色块行之下 6px。第一版拿 14dp 当负对照
        #    得到"通过", 我据此认定"缩行框会压到色块行", **那是错的**。真相是**字被切了**。
        #    教训: 负对照没红, 先怀疑自己的模型, 别怀疑工具有毛病。
        _, (ly0, ly1, lr) = "倒计时", got["倒计时"]
        if lr:
            print("")
            self._measure_reference(ly1 - ly0, lr)
            return
        self.stop()

    def _measure_reference(self, cur_s, cur_ink):
        """把行框开到 dp(40) 量参照笔画高, 再与当前值比 —— 比完恢复原值并停。"""
        self._keep_h = self.time_label.height
        self.time_label.height = dp(40)
        # ⚠️ **必须重新截一张** —— 第一版只改了高度就去找文件, 读到的还是旧图,
        #    于是拿"旧图 + 新行框坐标"去切带 ⇒ 参照笔画高量成 110px(整个带宽),
        #    正负两侧**都**报"切字了"。凡是"改完再量"的探针, 都要问一句
        #    "我量的那张图是改之后的吗"。
        Clock.schedule_once(lambda _d: Window.screenshot(name="%s_ref.png" % TAG), 0.3)
        Clock.schedule_once(lambda _d: self._finish_ref(cur_ink), 0.75)

    def _finish_ref(self, cur_ink):
        from PIL import Image
        # ⚠️ Kivy 把 `name="x.png"` 存成 `x0001.png`(**把 ".png" 剁掉再拼序号, 没有下划线**),
        #    所以前缀带 `_0` 的 glob 会一无所获(踩过: IndexError)。
        path = sorted(Path(".").glob("%s_ref*.png" % TAG))[-1]
        img = Image.open(path).convert("RGB")
        px = img.load()
        w, h = img.size
        bg = px[3, 3]
        y0 = int(round(TABLET_H - (self.time_label.y + self.time_label.height)))
        y1 = int(round(TABLET_H - self.time_label.y))
        hit = [y for y in range(max(0, y0), min(h, y1))
               if sum(1 for x in range(0, w, 3)
                      if max(abs(px[x, y][i] - bg[i]) for i in range(3)) > 18) > 2]
        ref = (hit[-1] - hit[0] + 1) if hit else 0
        cur = cur_ink[1] - cur_ink[0] + 1
        print("  == 判据: 笔画高 当前 %dpx vs 行框开到 40dp 的参照 %dpx  ⇒ %s"
              % (cur, ref, "通过(没切)" if cur >= ref - 1 else
                 "**不通过: 行框把字切掉了(矮了 %dpx)**" % (ref - cur)))
        self.stop()


Probe().run()
