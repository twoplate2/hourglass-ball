# -*- coding: utf-8 -*-
"""【1号】K5: 出生 x 重参数化**是否真的不动物理 RNG 流**。

做法: 把 `main.py` 拷成 `main_alt.py`(改一行), 同一 seed 各跑 N 帧,
比较 ① 末尾 `random.getstate()` ② 每颗粒的 vy(若随机流未动, vy 序列应当**逐位相同**)
 ③ px(应当**不同** —— 这正是改动本身)。

跑法: python tools/_p1_k5_rng.py
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "main.py").read_text(encoding="utf-8")
OLD = "                x_off = random.uniform(-x_clip, x_clip)\n"
NEW = ("                _u = random.uniform(-1.0, 1.0)\n"
       "                x_off = x_clip * (abs(_u) ** 1.6 if _u >= 0 else -(abs(_u) ** 1.6))\n")
assert OLD in SRC, "没找到 x_off 那一行 —— main.py 变了"
ALT = SRC.replace(OLD, NEW, 1)

TMP = Path(tempfile.mkdtemp(prefix="k5rng-"))
( TMP / "main_alt.py").write_text(ALT, encoding="utf-8")

RUNNER = r'''
import os, sys, json, random
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(sys.argv[1]); TMP = Path(sys.argv[2]); MOD = sys.argv[3]; SEED = int(sys.argv[4])
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(TMP))
os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
os.environ["KIVY_NO_ARGS"] = "1"; os.environ["KIVY_NO_FILELOG"] = "1"
os.environ["KIVY_HOME"] = str(TMP / "kivyhome")
import importlib
m = importlib.import_module(MOD)
from kivy.clock import Clock
from kivy.core.window import Window
m.HourglassWidget.load_config = lambda *_: {"duration": 50.0}
m.HourglassWidget.save_config = lambda *_: None
m.HourglassWidget._make_sound_proxy = lambda *_: None
m.HourglassWidget._make_completion_sound = lambda *_: None
m.HourglassApp.on_completed = lambda *_: None
now = [1000.0]
m.time = SimpleNamespace(perf_counter=lambda: now[0])
box = {}
class P(m.HourglassApp):
    def on_start(self):
        Clock.unschedule(self.hourglass.tick)
        Window.size = (400, 800)
        Clock.schedule_once(self.go, 0.6)
    def go(self, _dt):
        self.root.apply_orientation(); self.root.do_layout()
        self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
        w = self.hourglass
        w.set_duration(50.0); w.reset(); w._rebuild_height_table()
        assert w._geom_ready, '几何没就绪 —— 这一跑不作数'
        random.seed(SEED)
        _cnt = [0, 0]
        _seq = []
        _f = [0]
        _inst = random._inst
        _orig = _inst.random
        _origgb = _inst.getrandbits
        def _r():
            _cnt[0] += 1
            v = _orig()
            _seq.append((_f[0], v))
            return v
        def _gb(k):
            _cnt[1] += 1
            return _origgb(k)
        _inst.random = _r
        random.random = _r          # ⚠️ 模块级属性也要换: main.py 里 `rand = random.random` 抓的是它
        _inst.getrandbits = _gb
        w.toggle()
        _trace = []
        for _f[0] in range(900):
            now[0] += 1/60.0
            w.tick(1/60.0)
            if _f[0] % 25 == 0:
                _st = random.getstate()
                _trace.append([_f[0], _cnt[0], int(_st[1][-1]), w.pn])
        st = random.getstate()
        box["n"] = w.pn
        box["dbg"] = [w.running, w.elapsed, w._geom_ready, w.duration, getattr(w, "particle_acc", None)]
        box["vy"] = [float(v) for v in w.pvy[:w.pn]]
        box["px"] = [float(v) for v in w.px[:w.pn]]
        box["py"] = [float(v) for v in w.py[:w.pn]]
        box["state"] = [st[0], list(st[1][:8]), st[1][-1], st[2]]
        box["trace"] = _trace
        box["seq0"] = float(_seq[0][1]) if _seq else None
        box["seq"] = _seq[:4000] + _seq[-2000:]
        box["draws"] = _cnt[0]
        box["gb"] = _cnt[1]
        box["full"] = list(st[1][:-1])   # 624 字状态数组
        box["idx"] = int(st[1][-1])
        self.stop()
P().run()
print("@@JSON@@" + json.dumps(box))
'''

(TMP / "runner.py").write_text(RUNNER, encoding="utf-8")


def run(mod, seed=20261009):
    import subprocess
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, str(TMP / "runner.py"), str(ROOT), str(TMP), mod,
                        str(seed)], capture_output=True, env=env, timeout=900)
    out = r.stdout.decode("utf-8", "replace")
    if "@@JSON@@" not in out:
        print(out[-3000:], r.stderr.decode("utf-8", "replace")[-3000:])
        raise SystemExit("runner 失败")
    import json
    return json.loads(out.split("@@JSON@@", 1)[1].strip().splitlines()[0])


a = run("main")
c = run("main")            # 确定性对照: 同码同 seed 必须一模一样
b = run("main_alt")
print("")
sa, sb = a["seq"], b["seq"]
first = None
for i, (x, y) in enumerate(zip(sa, sb)):
    if x != y:
        first = (i, x, y); break
print("  随机值序列: 前 %d 项里首个不同 = %s  (共比 %d 项)" % (len(sa), first, min(len(sa), len(sb))))
print("  确定性对照(同码同 seed): 粒子数 %d/%d  draws %d/%d  state 相同 %s"
      % (a["n"], c["n"], a["draws"], c["draws"], a["state"] == c["state"]))
print("  粒子数   : 原 %d   改 %d" % (a["n"], b["n"]))
print("  random() 调用次数 : 原 %d   改 %d   (差 %d)"
      % (a["draws"], b["draws"], b["draws"] - a["draws"]))
print("  getrandbits 次数  : 原 %d   改 %d   (差 %d)"
      % (a["gb"], b["gb"], b["gb"] - a["gb"]))
print("  624 字状态数组相同 : %s" % (a["full"] == b["full"]))
print("  状态索引 index    : 原 %d   改 %d" % (a["idx"], b["idx"]))
print("")
print("  逐 25 帧轨迹 [帧, 累计 random() 次数, MT index, 存活粒子] 首个不同的行:")
for ra, rb in zip(a["trace"], b["trace"]):
    mark = "" if ra == rb else "   <<< 分叉"
    print("    %s   %s%s" % (ra, rb, mark))
    if ra != rb:
        break
same_state = a["state"] == b["state"]
print("  末尾 random.getstate() 相同 : %s" % same_state)
print("  vy 序列逐位相同             : %s" % (a["vy"] == b["vy"]))
print("  py 序列逐位相同             : %s" % (a["py"] == b["py"]))
print("  px 序列逐位相同             : %s   (应当 False —— 那就是这次改动)" % (a["px"] == b["px"]))
if a["vy"] != b["vy"]:
    for i, (u, v) in enumerate(zip(a["vy"], b["vy"])):
        if u != v:
            print("    首个 vy 不同: 第 %d 颗  %r vs %r" % (i, u, v)); break
if a["px"] != b["px"]:
    for i, (u, v) in enumerate(zip(a["px"], b["px"])):
        if u != v:
            print("    首个 px 不同: 第 %d 颗  %.3f vs %.3f" % (i, u, v)); break
print("  状态摘要 原 =", a["state"][0], a["state"][1][:4], a["state"][2])
print("  状态摘要 改 =", b["state"][0], b["state"][1][:4], b["state"][2])
shutil.rmtree(TMP, ignore_errors=True)
