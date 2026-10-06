"""r37 通用控件定位: 从截图里按颜色找连通块 bbox, 不目测坐标。

用法:
  python tools/_r37_find.py <shot.png> <hexcolor> [tol] [minarea]
"""
import sys
from PIL import Image
import numpy as np


def find_regions(path, hexcolor, tol=12, minarea=2000):
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(np.int16)
    h = hexcolor.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    d = (np.abs(a[:, :, 0] - r) <= tol) & (np.abs(a[:, :, 1] - g) <= tol) & (np.abs(a[:, :, 2] - b) <= tol)
    return mask_regions(d, minarea)


def mask_regions(mask, minarea=2000):
    H, W = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return out
    # 用扫描线做连通域 (4-连通), 逐行 union-find 太慢 -> 用简单的 BFS 分批
    from collections import deque
    idx = np.zeros((H, W), dtype=bool)
    idx[ys, xs] = True
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        q = deque([(y0, x0)])
        seen[y0, x0] = True
        n = 0
        miny = maxy = y0
        minx = maxx = x0
        while q:
            y, x = q.popleft()
            n += 1
            if y < miny: miny = y
            if y > maxy: maxy = y
            if x < minx: minx = x
            if x > maxx: maxx = x
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < H and 0 <= nx < W and idx[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if n >= minarea:
            out.append((minx, miny, maxx, maxy, n))
    out.sort(key=lambda t: -t[4])
    return out


if __name__ == "__main__":
    p = sys.argv[1]
    hexc = sys.argv[2]
    tol = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    ma = int(sys.argv[4]) if len(sys.argv) > 4 else 2000
    for (x0, y0, x1, y1, n) in find_regions(p, hexc, tol, ma):
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        print(f"bbox=({x0},{y0})-({x1},{y1}) size={x1-x0+1}x{y1-y0+1} area={n} center=({cx},{cy})")
