"""Probe: how far does mound_peak_offset actually wander? (attacks A5)
Runs real update_particles (no App loop) at fixed 60Hz and logs the hit-x EMA."""
import os, sys, tempfile, math
from pathlib import Path
from types import SimpleNamespace
import random, statistics as st

ROOT = Path(__file__).resolve().parents[1]
os.environ["KIVY_HOME"] = tempfile.mkdtemp(prefix="ema-probe-")
os.environ["KIVY_NO_ARGS"] = "1"; os.environ["KIVY_NO_FILELOG"] = "1"
os.environ["KIVY_METRICS_DENSITY"] = "1"; os.environ["KIVY_METRICS_FONTSCALE"] = "1"
sys.path.insert(0, str(ROOT))
from kivy.config import Config
Config.set('graphics', 'width', '400'); Config.set('graphics', 'height', '800')
import main as module
module.HourglassWidget._make_sound_proxy = lambda *_: None
module.HourglassWidget._make_completion_sound = lambda *_: None
T = [1000.0]
module.time = SimpleNamespace(perf_counter=lambda: T[0])
from kivy.base import EventLoop
EventLoop.ensure_window()

def run(period, width=400, height=800, wall=30.0, dt=1/60):
    w = module.HourglassWidget()
    w.size = (width, height)
    w.duration = float(period)
    random.seed(23)
    w.running = True; w.elapsed = 0.0
    offs, tops, hits = [], [], []
    seen = set()
    t = 0.0
    max_steps = int(wall/dt)
    i = 0
    while i < max_steps and t < w.duration*0.98:
        i += 1; t += dt
        T[0] += dt
        w.elapsed = t
        w.update_particles(dt)
        for f in w.flares:
            if id(f) not in seen:
                seen.add(id(f)); hits.append(f["x"] - w._cx)
        if i % 3 == 0:
            offs.append(w.mound_peak_offset)
            tops.append(w.get_mound_top_y() - w._lower_sand_bot)
    # settle window: last 15 s of the run (that is what a viewer holds in view)
    tail = offs[-int(15/(dt*3)):]
    print(f"period={period:g}s  sim={t:.1f}s  frames={i}  particles={w.pn}  neck_w={w.neck_w} ow={w._ow:.1f} sf={w.speed_factor:.2f}")
    if tail:
        print(f"  EMA last15s: min={min(tail):+.2f} max={max(tail):+.2f} peak-to-peak={max(tail)-min(tail):.2f} sd={st.pstdev(tail):.2f}")
    if offs:
        print(f"  EMA whole : min={min(offs):+.2f} max={max(offs):+.2f} p2p={max(offs)-min(offs):.2f}")
    if hits:
        s=sorted(hits)
        print(f"  hit-x n={len(hits)} sd={st.pstdev(hits):.2f} p5={s[len(s)//20]:+.1f} p95={s[len(s)*19//20]:+.1f} min={s[0]:+.1f} max={s[-1]:+.1f}")
    if tops:
        print(f"  mound top px: t=1s {tops[min(len(tops)-1,max(0,int(1/(dt*3))))]:.1f}  end {tops[-1]:.1f}  (inner diam {2*w._R_inner:.0f}px)")

for period in (1.0, 5.0, 10.0, 60.0, 600.0, 3600.0):
    run(period)
print("--- phone geometry (1080 wide) ---")
for period in (1.0, 10.0, 3600.0):
    run(period, width=1080, height=2160, wall=12.0)
