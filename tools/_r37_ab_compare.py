# -*- coding: utf-8 -*-
"""配方③ A/B 判定 + 并排图。

三条判据(专家给的):
  ① 横屏带内比值 1.000 -> 0.302(用 `_r37_ab_capture.py` 的 before/after 两张图逐列算,
     不需要再跑一次空窗基线 —— 比值就是 after/before);
  ② 弹窗卡片内部 **逐像素最大通道差 = 0**;
  ③ 竖屏 **整幅逐像素 0 差异**。

跑法: python tools/_r37_ab_compare.py
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SHOT = ROOT / "_shot" / "r37"

# 各 case 的弹窗几何(层/窗口坐标系, 由 capture 打印) —— 用来算"卡片保护区"
# (w, h) 窗口; land=True 时层绕屏心转 +90°
CASES = {
    "land_duration": ((2400, 1080), True,  (724, 138, 950, 803)),
    "port_duration": ((1080, 2400), False, (64, 798, 950, 803)),
    "land_dev":      ((2400, 1080), True,  (692, 170, 1015, 738)),
    "port_dev":      ((1080, 2400), False, (32, 36, 1015, 738)),
    "land_sound":    ((2400, 1080), True,  (724, 285, 950, 509)),
}


def card_rect_in_image(win, land, pop):
    """卡片在**图像坐标**(自上而下)里的包围盒, 内缩 4px 避开抗锯齿边。"""
    W, H = win
    x, y, w, h = pop
    if land:
        cx, cy = W / 2.0, H / 2.0
        pts = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
        xs, ys = [], []
        for px, py in pts:                      # 正变换 = 绕屏心逆时针 90
            xs.append(cx - (py - cy))
            ys.append(cy + (px - cx))
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
    else:
        x0, x1, y0, y1 = x, x + w, y, y + h
    r0, r1 = H - y1, H - y0                     # Kivy y -> 图像行
    return (int(x0) + 4, int(x1) - 4, int(r0) + 4, int(r1) - 4)


def load(tag, case):
    p = SHOT / ("%s_%s.png" % (tag, case))
    if not p.exists():
        return None
    return np.asarray(Image.open(p).convert("RGB")).astype(int)


def main():
    ok = True
    for case, (win, land, pop) in CASES.items():
        a, b = load("before", case), load("after", case)
        print("")
        print("=== %s  %dx%d  %s ===" % (case, win[0], win[1], "横屏" if land else "竖屏"))
        if a is None or b is None:
            print("  !! 缺图(before=%s after=%s)" % (a is not None, b is not None))
            ok = False
            continue
        if a.shape != b.shape:
            print("  !! 尺寸不等 %s vs %s" % (a.shape, b.shape))
            ok = False
            continue
        H, W, _ = a.shape
        diff = np.abs(a - b).max(axis=2)
        n = int((diff > 0).sum())
        print("  FULL-DIFF px %d / %d  maxchan %d" % (n, H * W, int(diff.max())))
        if n:
            ys, xs = np.nonzero(diff)
            print("        差异范围 x %d~%d  y(图像行) %d~%d" % (xs.min(), xs.max(), ys.min(), ys.max()))
        # ② 卡片内部
        x0, x1, r0, r1 = card_rect_in_image(win, land, pop)
        cd = diff[r0:r1, x0:x1]
        print("  CARD-DIFF x %d~%d y %d~%d px %d maxchan %d  %s"
              % (x0, x1, r0, r1, int((cd > 0).sum()), int(cd.max()) if cd.size else -1,
                 "OK-zero" if cd.max() == 0 else "**FAIL**"))
        if cd.max() != 0:
            ok = False
        # ③ 竖屏整幅
        if not land:
            # ⚠️ 整幅判据要**排除底部那条(底栏)** —— 配方② 会改版本号字号/按钮宽度, 那是
            #    另一次有意的改动, 不该算进弹窗的回归闸门。底栏 = 屏幕最下 6%。
            strip = int(H * 0.06)
            d2 = diff[:H - strip, :]
            print("  PORTRAIT-FULL(excl 底栏最下6%%) %s (maxchan %d) | 底栏条内差异像素 %d"
                  % ("OK-zero" if d2.max() == 0 else "**FAIL**", int(d2.max()),
                     int((diff[H - strip:, :] > 0).sum())))
            if d2.max() != 0:
                ok = False
        # ① 逐列带内比值(只对横屏)
        if land:
            band = np.concatenate([a[:max(1, int(H * 0.06))], a[-max(1, int(H * 0.06)):]], axis=0)
            bandb = np.concatenate([b[:max(1, int(H * 0.06))], b[-max(1, int(H * 0.06)):]], axis=0)
            la, lb = band.mean(axis=(0, 2)), bandb.mean(axis=(0, 2))
            ratio = np.where(la > 1, lb / np.maximum(la, 1e-6), 1.0)
            # 原先"未压暗"的带: 最左/最右各 (长边-短边)/2
            bw = int((max(win) - min(win)) / 2)
            left, right = ratio[:bw], ratio[W - bw:]
            print("  BAND-L/R (%d px each) %.3f / %.3f  (expect ~0.302 dimmed)"
                  % (bw, float(np.median(left)), float(np.median(right))))
            mid = ratio[bw:W - bw]
            print("  BAND-MID %.3f (expect ~1.000 untouched)" % float(np.median(mid)))
        # 并排图(横向拼接, 缩到 1/2 便于看)
        side = np.concatenate([a, np.full((H, 8, 3), 255), b], axis=1).astype("uint8")
        im = Image.fromarray(side)
        im = im.resize((im.width // 2, im.height // 2), Image.LANCZOS)
        SHOT.mkdir(parents=True, exist_ok=True)
        out = SHOT / ("SIDEBYSIDE_%s.png" % case)
        im.save(out)
        print("  并排图(左=改前 右=改后): %s" % out)
    print("")
    print("VERDICT: %s" % ("ALL-PASS" if ok else "**FAILURES**"))
    return 0 if ok else 1


def compare_bars():
    """配方② 底栏: 裁底部 22%(底栏所在)出并排图 + 报差异规模。"""
    for case in ("bar_320", "bar_360", "bar_411", "bar_A"):
        a, b = load("before", case), load("after", case)
        print("")
        print("=== %s (底栏) ===" % case)
        if a is None or b is None:
            print("  !! 缺图")
            continue
        H, W, _ = a.shape
        r0 = int(H * 0.78)
        ca, cb = a[r0:], b[r0:]
        d = np.abs(ca - cb).max(axis=2)
        print("  BAR-CROP %dx%d diffpx %d (%.1f%%) maxchan %d"
              % (cb.shape[1], cb.shape[0], int((d > 0).sum()),
                 100.0 * (d > 0).mean(), int(d.max())))
        side = np.concatenate([ca, np.full((ca.shape[0], 6, 3), 255), cb], axis=1).astype("uint8")
        p = SHOT / ("SIDEBYSIDE_%s.png" % case)
        Image.fromarray(side).save(p)
        print("  并排图(左=改前 右=改后): %s" % p)


if __name__ == "__main__":
    rc = main()
    compare_bars()
    sys.exit(rc)
