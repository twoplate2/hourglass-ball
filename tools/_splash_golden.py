# -*- coding: utf-8 -*-
"""飞溅层的**金标准轨迹**守卫 —— 改存储/改写法的前后必须逐位相同。

## 为什么需要它

飞溅的"贴坡滑 → 停住 → 原地留一会儿再消失"这套行为是用户**反复调过多轮**的。要动它的
存储结构(list[dict] → 并行数组)或积分写法, 唯一安全的做法是**证明逐位不变** ——
靠肉眼看是看不出来的, 而它一旦漂移就是**行为回归**。
`tools/test_physics_equiv.py` 只覆盖**主流粒子**, 不覆盖飞溅。

## 🔴 第一版有三个"看着有结论、其实答错了题"的毛病(2026-10-07 被对抗审查揪出)

1. **基线不可证伪**: 存在 `benchmark_logs/splash_golden.json`, 而 `benchmark_logs/` 在
   `.gitignore` 里, 且 `--save` 一键覆盖 ⇒ **"我跑过 --check 是绿的"在 git 里看不到任何痕迹**。
   ⇒ 现在基线是**紧凑指纹**(每帧一个 16 位哈希 + 计数), 存在 **`tools/splash_golden.fp.json`,
   进版本控制**。121 MB 的完整轨迹只在 `--trace-out` 时另存, 不入库。
2. **`_freeze` 用 `repr()` 比浮点, 会被"表示差异"骗**: `repr(np.float64(0.1))` 是
   `'np.float64(0.1)'` —— 而 `np.float64` **是 `float` 的子类**, 所以 `isinstance(v, float)`
   判据**拦不住**它。存储一换、字段物化成 numpy 标量, 守卫就**因为表示而非物理**翻红,
   逼人重建基线 —— 那一键会把**真漂移一起洗白**。
   ⇒ 现在比较前统一 `float()` 归一(见 `_freeze`), 且**值差异与计数差异分开报**。
3. **盲区一片**: 原来只记 `len(flares)`, 完全不记 `dusts`, 也不记随机数状态。
   flare 的 `x/y/end` 改错了、尘埃的积分改错了、随机流错位一帧 —— **指纹一位不变**。
   ⇒ 现在三者都记。

## 还剩下的已知盲区(**写在这里, 不许假装测过**)

- **默认配置下恒假的分支测不到**: `SPLASH_SLOPE_GAIN > 0.0`(默认 0.0)、`SPLASH_G_SCALE < 1.0`
  (默认 1.0)、`SPLASH_BG_PER_PARTICLE > 0`(默认 0.0)。`ARMS` 里为第一条单独跑一趟
  (`HG_SPLASH_SLOPE=1.0`); 后两条没跑。
- **渲染读端不在本守卫范围内**: 它直接读 `widget.splashes`, 不走 `_sync_rects` /
  `SplashBatch.update`。**安卓的批处理渲染路径这里一次都没走到** —— 那条要另走像素闸门。
- **窗口只有一个尺寸**(400×800)与一个密度。

## 跑法

    python tools/_splash_golden.py --save          # 存指纹(进 git)
    python tools/_splash_golden.py --check         # 改完之后比
    python tools/_splash_golden.py --check --trace-out /tmp/new.json
                                                   # 另存完整轨迹(调试用, 不入库)

退出码 0 = 一致; 非 0 = 有差异(打印首个不符的**帧号**与**至少一项计数**)。
"""
import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "tools" / "splash_golden.fp.json"     # ★ 进 git 的紧凑指纹

# 用例: (周期, 帧数)。1s 走完"注满→漏完→尘埃"; 15s 走"注满→下落→触底→贴坡→停住";
# 120s 走"长周期下的慢速累积"(表查找的钳位支与更早的触底时刻)。
CASES = ((1.0, 240), (15.0, 600), (120.0, 600))

# 额外臂: (标签, 环境变量)。默认配置下为假的分支必须单独跑一趟才谈得上"验过"。
ARMS = (("default", {}), ("slope1", {"HG_SPLASH_SLOPE": "1.0", "HG_SPLASH_GRAV_LO": "0.62"}))
TAG_WIDTH = 4.0      # 标识用, 无计算意义


def _freeze(value):
    """规范形式: 浮点先 `float()` 归一(**拦掉 np.float64 的 repr 差异**), 再取 repr。"""
    if isinstance(value, dict):
        return {k: _freeze(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_freeze(v) for v in value]
    if isinstance(value, float):
        # ⚠️ 必须先 float(): `np.float64` 是 float 的子类, isinstance 拦不住,
        #    直接 repr 会得到 'np.float64(0.1)' ⇒ **表示差异伪造成物理差异**。
        return repr(float(value))
    if hasattr(value, "__float__") and not isinstance(value, (bool, int, str)):
        return repr(float(value))
    return value


def _frame_payload(w):
    return {
        "n": len(w.splashes),
        "pn": w.pn,
        "nd": len(w.dusts),
        "g": [_freeze(s) for s in w.splashes],
        # ★ flares 记**内容**不只记个数 —— 原来 `"x": hy` 改成 `"y": _surf` 这种漂移
        #   在只记 len 时指纹一位不变。
        "f": [_freeze(f) for f in w.flares],
        "d": [_freeze(d) for d in w.dusts],
        "acc": repr(float(w._bg_splash_acc)),
        # ★ 随机数状态 —— 这是**唯一能在"错位发生的那一帧"就报警**的判据。
        #   飞溅循环本身不调随机数, 但生成路径调(且 `random.choice` 走拒绝采样,
        #   底层抽取次数是**数据相关**的, 实测 5~15 次/颗) —— 任何"顺手少抽一次"的
        #   改写都会让此后所有随机数错位。
        "rng": hashlib.sha256(repr(random.getstate()).encode()).hexdigest()[:12],
    }


def _digest(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def run_case(period, frames):
    """跑一个用例, 返回 `(每帧 (哈希, 计数), 完整逐帧载荷)`。"""
    with tempfile.TemporaryDirectory(prefix="splash-golden-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App                            # noqa: F401
        from kivy.clock import Clock
        from kivy.core.window import Window
        import main as module

        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": period}
        module.HourglassWidget.save_config = lambda *_: None
        # 假时钟: 步长必须**完全确定**, 否则两次跑的 dt 不同, 比对没有意义。
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])

        state = {"rows": [], "full": []}

        class ProbeApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.setup, 1.0)

            def setup(self, _dt):
                Window.size = (400, 800)
                Clock.schedule_once(self.go, 0.4)

            def go(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                w.completion_enabled = False
                w.set_duration(period)
                w._rebuild_height_table()
                w.reset()
                random.seed(23)                 # 与闸门/取证工具同一颗种子
                w.toggle()
                for _ in range(frames):
                    now[0] += 1 / 60.0
                    w.tick(1 / 60.0)
                    p = _frame_payload(w)
                    state["rows"].append((_digest(p), p["n"], p["pn"], p["nd"]))
                    state["full"].append(p)
                self.stop()

        ProbeApp().run()
        return state["rows"], state["full"]


def run_arm(tag, env, as_subprocess):
    """跑一个臂的全部用例。非默认臂走**子进程**(模块级常量在 import 时就读了环境变量)。"""
    if as_subprocess:
        with tempfile.TemporaryDirectory(prefix="splash-arm-") as tmp:
            out = Path(tmp) / "arm.json"
            e = dict(os.environ)
            e.update(env)
            e["SPLASH_GOLDEN_JSON_OUT"] = str(out)
            r = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                "--json-out", str(out)],
                               env=e, cwd=str(ROOT), capture_output=True)
            if not out.exists():
                raise RuntimeError("臂 %s 子进程没产出: rc=%d\n%s"
                                   % (tag, r.returncode, r.stderr.decode("utf-8", "replace")[-800:]))
            data = json.loads(out.read_text(encoding="utf-8"))
            # 统一形状: {周期: {"rows": [...], "full": None}} —— 子进程臂不传完整轨迹
            return {k: {"rows": v, "full": None} for k, v in data["rows"].items()}
    cases = {}
    for period, frames in CASES:
        rows, full = run_case(period, frames)
        cases["%g" % period] = {"rows": rows, "full": full}
    return cases


def _fingerprint_rows(cases):
    """`cases` = {周期: [(哈希, n, pn, nd), ...]} —— 逐帧哈希串成总指纹。"""
    h = hashlib.sha256()
    for key in sorted(cases):
        for row in cases[key]:
            h.update(json.dumps(row).encode("utf-8"))
    return h.hexdigest()[:16]


def fingerprint(cases):
    return _fingerprint_rows({k: cases[k]["rows"] for k in cases})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json-out", default=None,
                    help="(内部)子进程臂用: 只跑**当前环境**对应的那一臂并落盘")
    args = ap.parse_args()

    # ⚠️ **子进程臂必须在这里就返回** —— 否则子进程会再遍历一遍 `ARMS`,
    #    而其中一个臂又要开子进程 ⇒ 无限递归。
    if args.json_out:
        cases = {}
        for period, frames in CASES:
            rows, _full = run_case(period, frames)
            cases["%g" % period] = rows
        Path(args.json_out).write_text(
            json.dumps({"fp": fingerprint({"c": {"rows": rows}})
                        if False else _fingerprint_rows(cases),
                        "rows": cases}, ensure_ascii=False), encoding="utf-8")
        return 0

    results = {}
    for tag, env in ARMS:
        # 默认臂在**本进程**跑; 其余臂的环境变量必须在 `import main` **之前**生效 ⇒ 子进程
        cases = run_arm(tag, env, as_subprocess=bool(env))
        results[tag] = {"fp": _fingerprint_rows({k: cases[k]["rows"] for k in cases}),
                        "rows": {k: cases[k]["rows"] for k in cases}}
        print("臂 %-8s 指纹 %s" % (tag, results[tag]["fp"]))
        for key in sorted(cases):
            rows = cases[key]["rows"]
            print("   周期 %-6s 帧 %-4d 末帧: 飞溅 %d / 主粒子 %d / 尘埃 %d"
                  % (key, len(rows), rows[-1][1], rows[-1][2], rows[-1][3]))

    if args.save:
        STORE.write_text(json.dumps({"arms": results}, ensure_ascii=False, sort_keys=True),
                         encoding="utf-8")
        print("已存指纹基线 ->", STORE, "(%.1f KB)" % (STORE.stat().st_size / 1024.0))
        return 0

    if args.check:
        if not STORE.exists():
            print("!! 没有基线, 先跑 --save")
            return 1
        old = json.loads(STORE.read_text(encoding="utf-8"))["arms"]
        bad = False
        for tag in results:
            if tag not in old:
                print("!! 基线里没有臂 %s" % tag)
                bad = True
                continue
            if old[tag]["fp"] == results[tag]["fp"]:
                print("一致  臂 %-8s %s" % (tag, results[tag]["fp"]))
                continue
            bad = True
            print("!! 不一致 臂 %s: 基线 %s vs 现在 %s"
                  % (tag, old[tag]["fp"], results[tag]["fp"]))
            for key in sorted(results[tag]["rows"]):
                a = old[tag]["rows"].get(key)
                b = results[tag]["rows"][key]
                if a == b:
                    continue
                if a is None:
                    print("   周期 %s: 基线没有这一档" % key)
                    continue
                for i, (ra, rb) in enumerate(zip(a, b)):
                    if ra != rb:
                        print("   周期 %s 首处不符在第 %d 帧: 基线 %r vs 现在 %r"
                              % (key, i, ra, rb))
                        break
                else:
                    print("   周期 %s: 长度不同 %d vs %d" % (key, len(a), len(b)))
        print("==> 金标准轨迹", "有差异" if bad else "逐位一致")
        return 1 if bad else 0
    return 0


sys.exit(main())
