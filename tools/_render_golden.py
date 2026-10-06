# -*- coding: utf-8 -*-
"""**渲染金标准**: 把 `inspect_flow.py` 那套裁图做成**逐图指纹**, 存进 git 供 `--check`。

## 为什么必须补它(2026-10-07, 1.201 回退的教训)

1.201 把 `_replay_hits` 的接触高度改成整批向量化(净 −0.05ms)。当时:

- **隔离探针过了** —— `_probe_ctab_equiv` 300 组 × 62 点, 逐位 0 不一致;
- **金标准轨迹也过了** —— `_splash_golden` 两臂指纹一位不变。

**但逐像素比对显示 30 帧里有 1 帧不同。** 差异落在**两个守卫都没覆盖的工况**上:
前者的输入分布是我挑的, 后者只走 1s/15s/120s 三个周期 —— 而 `inspect_flow` 用的是
另一套(60s 桩 + 它自己的目标时刻集)。**没有任何一个守卫的输入集包含它。**

⇒ 结果只能靠我事后**手动**跑一次 `_pixdiff` 才发现。这条守卫把那次手动动作固化:
**凡改动渲染/打包/几何, 跑一次 `--check` 就知道像素有没有动。**

## 它记什么

对固定的渲染集(`--steady-period 15 --steady-frames 30`, 与取证工具同一套)逐图算
**SHA256**, 存 `tools/render_golden.fp.json`(**进 git**, 所以"我跑过是绿的"可证伪)。

⚠️ **屏蔽版本号区域**(`196,756-232,784`): 那个标签每改一次版本就变, 不屏蔽的话
每次提交都会"翻红", 于是这个守卫会被人习惯性地重建基线 —— 那就跟没有一样。
⚠️ 只覆盖**一套**工况(一个周期、一个帧数、一个尺寸)。要扩就先补 `CASES`, **别只放一套
就宣称"渲染没变"** —— 1.201 正是栽在"覆盖不到的那一档"上。

## 跑法

    python tools/_render_golden.py --save      # 存基线(改渲染**之前**跑)
    python tools/_render_golden.py --check     # 改完跑, 必须"逐图一致"
    python tools/_render_golden.py --check --label myrun   # 用别的标签, 便于保留现场

退出码 0 = 逐图一致。
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "tools" / "render_golden.fp.json"

# 固定的渲染集 —— 与 `tools/_pixdiff.py` 的默认屏蔽区**必须一致**
ARGS = ("--steady-period", "15", "--steady-frames", "30")
MASK = (196, 756, 232, 784)          # 底栏版本号
ENV = {"HG_SPLASH_RENDERER": "batch", "HG_NECK_RENDERER": "batch",
       "HG_FLOW_RENDERER": "texture"}
CASES = (("batch", ENV),
         # 非批处理那条路(桌面默认)也记一份 —— 它走的是 `_sync_rects_arrays`
         ("rect", {"HG_NECK_RENDERER": "line", "HG_FLOW_RENDERER": "texture"}))


def render(label, env):
    out = ROOT / "benchmark_logs" / ("flow_visual_" + label)
    if out.exists():
        shutil.rmtree(out)
    e = dict(os.environ)
    e.update(env)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "inspect_flow.py"),
                        "--label", label] + list(ARGS),
                       cwd=str(ROOT), env=e, capture_output=True)
    if not out.exists():
        raise RuntimeError("渲染没产出(%s): rc=%d\n%s"
                           % (label, r.returncode, r.stderr.decode("utf-8", "replace")[-600:]))
    return out


def image_hashes(d):
    """逐图算 SHA256(**先屏蔽版本号区**, 见模块头)。"""
    x0, y0, x1, y1 = MASK
    out = {}
    for name in sorted(os.listdir(d)):
        if not name.endswith(".png"):
            continue
        im = Image.open(d / name).convert("RGB")
        px = im.load()
        h = hashlib.sha256()
        h.update(("%dx%d|" % im.size).encode())
        for y in range(im.size[1]):
            if y0 <= y < y1:
                continue
            row = bytearray()
            for x in range(im.size[0]):
                if x0 <= x < x1:
                    continue
                row.extend(px[x, y])
            h.update(row)
        out[name] = h.hexdigest()[:16]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--label", default="rg")
    args = ap.parse_args()

    got = {}
    for tag, env in CASES:
        d = render("%s_%s" % (args.label, tag), env)
        got[tag] = image_hashes(d)
        print("%-6s 图 %d 张" % (tag, len(got[tag])))

    if args.save:
        STORE.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        print("已存渲染指纹 ->", STORE, "(%.1f KB)" % (STORE.stat().st_size / 1024.0))
        return 0

    if args.check:
        if not STORE.exists():
            print("!! 没有基线, 先跑 --save(在改渲染**之前**)")
            return 1
        old = json.loads(STORE.read_text(encoding="utf-8"))
        bad = 0
        for tag in got:
            a, b = old.get(tag), got[tag]
            if a is None:
                print("!! 基线里没有臂 %s" % tag)
                bad += 1
                continue
            diff = [k for k in b if a.get(k) != b[k]]
            miss = [k for k in a if k not in b]
            if not diff and not miss:
                print("一致  臂 %-6s %d 张" % (tag, len(b)))
                continue
            bad += 1
            print("!! 不一致 臂 %s: 差异 %d 张 / 缺 %d 张" % (tag, len(diff), len(miss)))
            for k in diff[:5]:
                print("     %s" % k)
        print("==> 渲染", "有差异" if bad else "逐图一致")
        return 1 if bad else 0
    return 0


sys.exit(main())
