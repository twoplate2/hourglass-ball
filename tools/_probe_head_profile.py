# -*- coding: utf-8 -*-
"""**头部形状**的量具 —— **WIP, 判据未标定, 不许当闸门用**。(2026-10-11)

## 它要补的洞(真的存在)

用户当天连问两次「为什么你的测试找不到此类bug」。实情: 统计量只取**一帧中段**、
金标准只比**哈希**(只问像素变没变、不问对不对, 而且每改必重标定 = 盖章放行)、
物理只管物理 —— **没有任何一道看头部的形状**。于是"开头一个很尖的 V ⇒ 突然变钝"
这种既明显又稳定的缺陷一路出货到 2.48。

## ⚠️ 但它现在**还不能用**

**四次尝试, 四次都在量错东西**(记在这里免得下一个人重走):

  1. 列范围取"头部以上有沙的所有列" ⇒ 头部沉进下半球后那一带只剩**球壁**,
     锥深一路涨到 263px(量的是球面);
  2. 头部判定只看"下方有没有宽行" ⇒ **画面最后一行**下方什么都没有, 于是把底边
     当成头部(头行恒 1899, 毛度 437);
  3. 反过来"先找堆顶再在其上方找窄行" ⇒ 头部行落到了**尖端那一行**, 而尖端那行
     跨度天然很窄 ⇒ 在它里面取轮廓必然是平的 ⇒ **锥深恒 0**;
  4. 列范围改成"头部上方 30 行那一带" ⇒ 终于能动了, 但**读数与人工读数对不上**
     (人工读同一段录像 = 稳定 13~15px; 它读 4~7px 还带 25 的尖刺)。

**⇒ 按项目规矩(「分不开/对不上的门槛不许当依据」)不接线、不进闸门。**

## 人工那一版是**能用的**(找到 40° 锥的就是它)

```python
# 对每帧: 取柱子那条竖带, 每列"最低的沙色行" → 减去该帧最低点 → 相对抬升
# 例(用户 2026-10-11 录像, 2.47): t=2.00 → 163 [13 4 0 3 6] 162  ⇒ 中轴平坦、两侧各抬 13~15px
#                                 在 18px 半宽上 = 约 40° 的锥
```
**下一步**: 把上面那段人工读法逐字搬进来(先不做"自动找头", 直接给行范围), 用
`已知错 = 用户录像` / `已知对 = 2.49 之后` 两侧标定通过之后再谈进闸门。
"""

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SAND = np.array([230.0, 170.0, 95.0])       # 金沙; 换配色要同步改
TOL = 75


def frames(src, every):
    p = Path(src)
    if p.is_dir():
        fs = sorted(p.glob("period-*.png"),
                    key=lambda q: float(q.stem.split("time-")[1]))
        return [(q.stem.split("time-")[1], np.asarray(Image.open(q).convert("RGB")))
                for q in fs[::every]]
    import cv2
    cap = cv2.VideoCapture(str(p))
    out, i = [], 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % every == 0:
            out.append(("%.2f" % (i / max(cap.get(cv2.CAP_PROP_FPS), 1)),
                        cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)))
        i += 1
    cap.release()
    return out


def profile(a, x0, x1, maxw=60, pile=16):
    """返回 (头部行, 轮廓(相对最低点的抬升), 柱内列索引)。

    🔴 **找头的顺序(第三版才对)**: ① 先找**沙堆顶** = 从下往上**第一个跨度 > 100 的行**
    (沙堆一定比柱子宽); ② 头部 = 堆顶**之上**、跨度 ∈ [6, `maxw`]、且**上面 10 行内有沙**
    的**最低**那一行。
    前两版分别栽在: (v1) 取"头部以上有沙的所有列" ⇒ 头部下沉后量到**球壁**(锥深涨到 263);
    (v2) 只看"下方有没有宽行" ⇒ 在**画面最后一行**下方什么都没有, 于是把底边当成头部
    (实测头行恒 1899)。**量具自己也要标定** —— 这一条是这三次的教训。
    """
    reg = a[:, x0:x1].astype(int)
    m = np.abs(reg - SAND[None, None, :]).max(axis=2) < TOL
    H = m.shape[0]
    span = np.zeros(H, dtype=int)
    for r in range(H):
        xs = np.nonzero(m[r])[0]
        span[r] = (xs[-1] - xs[0] + 1) if xs.size >= 5 else 0
    wide = np.nonzero(span > 100)[0]
    top = int(wide.min()) if wide.size else H          # 堆顶行(堆一定比柱宽)
    head = -1
    for r in range(top - 1, 20, -1):
        if 6 <= span[r] <= maxw and m[max(0, r - 10):r].any():
            head = r
            break
    if head < 0:
        return -1, None, None
    # 列范围 = **头部上方 `BACK` 行那一带的连续沙带**(= 柱身宽度), **不是尖端自己那行**
    #   —— 尖端那行的跨度天然很窄, 在它里面取轮廓必然是平的(第 3 版实测锥深恒 0)。
    BACK = 30
    rb = max(0, head - BACK)
    xs = np.nonzero(m[rb])[0]
    c0, c1 = int(xs[0]), int(xs[-1]) + 1
    prof = np.full(c1 - c0, -1)
    for c in range(c0, c1):
        ys = np.nonzero(m[:head + 1, c])[0]
        if ys.size:
            prof[c - c0] = ys.max()
    ok = np.nonzero(prof >= 0)[0]
    if ok.size < 8:
        return head, None, None
    rel = prof[ok[0]:ok[-1] + 1]
    return head, rel.max() - rel, ok


def main():
    src = sys.argv[1]
    x0 = int(sys.argv[sys.argv.index("--x0") + 1]) if "--x0" in sys.argv else 250
    x1 = int(sys.argv[sys.argv.index("--x1") + 1]) if "--x1" in sys.argv else 335
    every = int(sys.argv[sys.argv.index("--every") + 1]) if "--every" in sys.argv else 2

    print("== %s (每 %d 帧取一张) ==" % (src, every))
    print("%8s %8s %8s %8s %8s" % ("t", "头部行", "锥深", "毛度", "跳"))
    rows = []
    for t, a in frames(src, every):
        head, rel, ok = profile(a, x0, x1)
        if rel is None:
            continue
        n = len(rel)
        e = max(2, n // 6)
        cone = float(np.median(np.concatenate([rel[:e], rel[-e:]])))   # 两端抬升 = 锥深
        d = np.diff(rel.astype(float))
        rough = float(np.std(d - d.mean()))                            # 去趋势毛度
        rows.append((float(t), head, cone, rough))
    if not rows:
        print("  没找到自由头部(全落堆了?)")
        return 1
    prev = None
    cones = []
    for t, head, cone, rough in rows:
        jump = "" if prev is None else "%+8.1f" % (cone - prev)
        print("%8.2f %8d %8.1f %8.2f %s" % (t, head, cone, rough, jump))
        prev = cone
        cones.append(cone)
    c = np.array(cones)
    med, mx = float(np.median(c)), float(np.max(c))
    print("\n锥深: 中位 **%.1f** 最大 %.1f   (%d 帧)" % (med, mx, len(c)))
    print("判据(用户 2026-10-11 录像标定): 已知错 2.47 = 13~15px; 已知对 = ≤6px")
    print("==> %s" % ("OK (锥深中位 ≤ 8px)" if med <= 8.0 else
                      "!! 仍是明显的锥(中位 %.1f > 8px)" % med))
    return 0 if med <= 8.0 else 1


if __name__ == "__main__":
    sys.exit(main())
