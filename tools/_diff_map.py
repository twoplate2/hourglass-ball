# -*- coding: utf-8 -*-
"""**差异的"形状"** —— 把两个渲染集的不同逐像素打成 ASCII 网格(2026-10-07)。

## 为什么需要它(而不是又一个"差了多少像素")

2026-10-07 为颈部空壳色调组那 42 条指令做了 **6 次实验、排除 6 个假设**
(次序/位置、下标错位、尾部颜色残留、环境色状态残留、编译器分组、空组会画东西),
而**这些排除项合起来本该意味着"不该有任何差异"** —— 可 `_render_golden --check`
实测就是有。

⇒ **那个矛盾本身就是线索**: 原因不在"渲染管线"那一层, 更可能是**改动里还藏着一个
没被发现的行为差异**(某个色调档的颗粒数/位置对不上), 只是它以"像素不同"的形式表现出来。
⇒ **下一轮别猜机制, 先看差异长什么样**:
   · 整条沙面一起动 ⇒ 是**几何/位置**类
   · 只有某几颗小方块动了 ⇒ 是**颗粒分配**类(该进 A 档的进了 B 档)
   · 只有边界一像素 ⇒ 才是**渲染/抗锯齿**类

## 与 `_pixdiff.py` 的分工

`_pixdiff.py` 报"多少像素不同、最大通道差多少"(**计数**);
本工具报"**不同在哪里、成什么形状**"(**分布**)。判机制要看分布。

## 跑法

    python tools/_diff_map.py <标签A> <标签B> [图名子串] [网格宽=96] [阈值=2]

两个标签是 `tools/inspect_flow.py --label <标签>` 产出的目录名
(`benchmark_logs/flow_visual_<标签>/`)。按**文件名**配对; 找不到的会报出来。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 由暗到亮的"差异强度"字符 —— 与计数无关, 只表示**这一格里差得多大**
RAMP = " .:-=+*#%@"


def load(label):
    d = ROOT / "benchmark_logs" / ("flow_visual_" + label)
    if not d.is_dir():
        raise SystemExit("找不到 %s —— 先跑 inspect_flow.py --label %s" % (d, label))
    return {p.name: p for p in sorted(d.glob("*.png"))}


def run():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    la, lb = sys.argv[1], sys.argv[2]
    want = sys.argv[3] if len(sys.argv) > 3 else "neck-"
    cols = int(sys.argv[4]) if len(sys.argv) > 4 else 96
    thr = int(sys.argv[5]) if len(sys.argv) > 5 else 2

    a, b = load(la), load(lb)
    names = [n for n in sorted(set(a) & set(b)) if want in n]
    if not names:
        raise SystemExit("没有匹配 %r 的图; A=%d 张 B=%d 张" % (want, len(a), len(b)))
    only = sorted(set(a) ^ set(b))
    if only:
        print("!! 没配上的图 %d 张(各例: %s)" % (len(only), only[:3]))

    from PIL import Image
    for name in names:
        ia = Image.open(a[name]).convert("RGB")
        ib = Image.open(b[name]).convert("RGB")
        if ia.size != ib.size:
            print("!! %s 尺寸不同 %s vs %s" % (name, ia.size, ib.size))
            continue
        pa, pb = ia.load(), ib.load()
        w, h = ia.size
        rows = max(1, int(cols * h / max(1, w) / 2.2))     # 终端字符大致 2:1
        diff = strong = 0
        mx = 0
        grid = []
        for gy in range(rows):
            line = []
            for gx in range(cols):
                x0, x1 = gx * w // cols, max(gx * w // cols + 1, (gx + 1) * w // cols)
                y0, y1 = gy * h // rows, max(gy * h // rows + 1, (gy + 1) * h // rows)
                local = 0
                for y in range(y0, y1):
                    for x in range(x0, x1):
                        ca, cb = pa[x, y], pb[x, y]
                        d = max(abs(ca[0] - cb[0]), abs(ca[1] - cb[1]), abs(ca[2] - cb[2]))
                        if d:
                            diff += 1
                        if d > thr:
                            strong += 1
                        if d > local:
                            local = d
                        if d > mx:
                            mx = d
                idx = 0 if local == 0 else min(len(RAMP) - 1,
                                               1 + int(local / 256.0 * (len(RAMP) - 1)))
                line.append(RAMP[idx])
            grid.append("".join(line))
        print("")
        print("  === %s  (%dx%d, %d 格 x %d 格, 阈值 %d) ===" % (name, w, h, cols, rows, thr))
        print("  差异像素 %d, 差>%d 的 %d, 最大通道差 %d" % (diff, thr, strong, mx))
        print("  A=%s  B=%s    强度: '%s'(弱) -> '%s'(强), 空格=无差异"
              % (la, lb, RAMP[1], RAMP[-1]))
        print("")
        for line in grid:
            print("  |" + line + "|")
    return 0


sys.exit(run())
