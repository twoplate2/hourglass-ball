# -*- coding: utf-8 -*-
"""配方③ 前置: 验证**弹窗 `canvas.before` 的坐标系**到底是父(层)系还是弹窗局部系。

配方明确要求"写完先用一个小标记矩形验证坐标系, 再写正式代码": 按错的那套算, 遮罩会变成
一条 L 形亮带。本探针就量这一件事, 用两个**不同颜色**的小方块:

  M_A(红) pos=(1000, 400)                        ← 按"父(层)系"摆
  M_B(绿) pos=popup.pos + (1000, 400)            ← 按"弹窗局部系"摆

横屏 2400x1080, 层绕屏心 (1200,540) 反旋转 +90°(桌面 `_land_angle()` 兜底=90),
正变换 `_to_win(x,y) = (cx - (y-cy), cy + (x-cx))`:

  假设P(父系): 红方块 (1000..1120, 400..520) → 屏幕上 x∈[1220,1340] y∈[340,460]
  假设L(局部): 红方块先平移到 popup.pos 再转 → 落在别处(实测见输出)

判据: 红方块出现在 **x∈[1220,1340] y∈[340,460]** ⇒ 父系成立(配方③ 的算法可直接用)。

跑法: python tools/_r37_marker_xy.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "_shot" / "marker"


def main():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    w, h = 2400, 1080
    sys.argv = [sys.argv[0], "_marker", "--landscape"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics import Color, Rectangle
    from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
    from PIL import Image
    import numpy as np

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": 50}
    m.HourglassWidget.save_config = lambda *_: None

    res = {"pos": None}
    orig_open = m._SandBgPopup.open

    def patched_open(self, *a, **kw):
        ret = orig_open(self, *a, **kw)
        res["pos"] = tuple(self.pos)
        res["size"] = tuple(self.size)
        res["parent"] = type(self.parent).__name__
        # ⚠️ 用 canvas.after 不是为了改坐标系 —— before/after 画的是**同一个坐标系**
        #    (都是父/层的系), 只有画家顺序不同。放 after 只是为了让标记盖在
        #    弹窗自带遮罩(α0.7)与卡片之上, 颜色不被压暗, 便于按纯色找包围盒。
        with self.canvas.after:
            Color(1, 0, 0, 1)
            Rectangle(pos=(1000, 400), size=(120, 120))
            Color(0, 1, 0, 1)
            Rectangle(pos=(self.pos[0] + 1000, self.pos[1] + 400), size=(120, 120))
        return ret

    m._SandBgPopup.open = patched_open

    def shot():
        ww, hh = int(Window.width), int(Window.height)
        px = glReadPixels(0, 0, ww, hh, GL_RGBA, GL_UNSIGNED_BYTE)
        return np.asarray(Image.frombytes("RGBA", (ww, hh), px)
                          .transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")).astype(int)

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (w, h)
            Clock.schedule_once(self.s1, 1.4)

        def s1(self, _dt):
            self.on_duration_picker(None)
            Clock.schedule_once(self.s2, 1.0)

        def s2(self, _dt):
            img = shot()
            OUT.mkdir(parents=True, exist_ok=True)
            Image.fromarray(img.astype("uint8")).save(OUT / ("marker_%dx%d.png" % (w, h)))
            res["img"] = img
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    return res


def bbox(img, rgb, tol=40):
    import numpy as np
    d = np.abs(img - np.array(rgb)).max(axis=2)
    ys, xs = np.nonzero(d < tol)
    if not len(ys):
        return None
    return (int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()))


if __name__ == "__main__":
    import numpy as np
    r = main()
    img = r.get("img")
    print("")
    print("  === 标记方块坐标系验证 (2400x1080, 反旋转 +90°) ===")
    print("  弹窗 pos=%s size=%s 宿主=%s" % (r.get("pos"), r.get("size"), r.get("parent")))
    if img is None:
        print("  !! 没抓到图")
        raise SystemExit(1)
    red = bbox(img, (255, 0, 0))
    grn = bbox(img, (0, 255, 0))
    print("  M_A(按父系摆 (1000,400)) 屏幕落点 x%s y%s" %
          ((red[0], red[1]) if red else None, (red[2], red[3]) if red else None))
    print("  M_B(按局部系摆 pos+(1000,400)) 屏幕落点 x%s y%s" %
          ((grn[0], grn[1]) if grn else None, (grn[2], grn[3]) if grn else None))
    print("  预测(假设P 父系): M_A 应落在 x[1220,1340] y[340,460](Kivy y 向上)")
    print("                     -> **图像行** y[%d,%d]" % (1080 - 460, 1080 - 340))
    # ⚠️ bbox 给的是**图像行**(自上而下), Kivy 的 y 是自下而上 ⇒ 比较前要翻
    if red and 1210 <= red[0] and red[1] <= 1350 and (1080 - red[3]) >= 330 and (1080 - red[2]) <= 470:
        print("  => **父(层)坐标系成立**(M_A 落在预测处, 与假设P 逐像素吻合)"
              " —— 配方③ 的 pos/α 算法可直接用")
    else:
        print("  => **父系假设不成立**! 按局部系重算 (配方里的坑)")
