# -*- coding: utf-8 -*-
"""`_sync_rects` 里"值没变就不写"那道守卫的**实际命中率** —— 外部评审 §3.3 的质疑。

评审: 「`_sync_rects` 通过 `enumerate(particles)` 把列表第 i 颗映射到池第 i 个图元。
只要前面的粒子被删除、存活列表被压紧, **后面的静止粒子也可能换槽** ⇒
'坐标没变就不写'的优化命中率会下降。」

本探针在真实运行中统计: 遍历次数 / 实际写入次数 / **静止(未移动)但被迫重写**的次数。

跑法: python tools/_probe_rect_guard.py [周期=15]
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0


def run():
    os.environ.setdefault("KIVY_METRICS_DENSITY", "1.75")
    os.environ["HG_FLOW_RENDERER"] = "texture"
    sys.argv = [sys.argv[0], "_guard"]
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": PERIOD}
    m.HourglassWidget.save_config = lambda *_: None

    ST = {"n": 0, "wrote": 0, "still": 0, "still_rewritten": 0, "hold": 0}
    orig = m.HourglassWidget._sync_rects
    frames = [0]

    def sync(group, pool, particles, fixed_size=None):
        ST["n"] += len(particles)
        for i, p in enumerate(particles):
            if i < len(pool):
                r = pool[i]
                sz = p["size"] if fixed_size is None else fixed_size
                w = float(sz[0]) if isinstance(sz, (tuple, list)) else float(sz)
                h = float(sz[1]) if isinstance(sz, (tuple, list)) else float(sz)
                pos = (p["x"] - w / 2, p["y"] - h / 2)
                moved = abs(r.pos[0] - pos[0]) > 1e-9 or abs(r.pos[1] - pos[1]) > 1e-9
                if p.get("_still") is not None:
                    ST["still"] += 1
                    if moved:
                        ST["still_rewritten"] += 1
                if moved:
                    ST["wrote"] += 1
                else:
                    ST["hold"] += 1
        return orig(group, pool, particles, fixed_size)

    m.HourglassWidget._sync_rects = staticmethod(sync)

    class P(m.HourglassApp):
        def on_start(self):
            Window.size = (400, 800)
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            hg = self.hourglass
            hg.set_duration(PERIOD)
            hg._rebuild_height_table()
            hg.reset()
            hg.toggle()
            Clock.schedule_once(lambda dt: self.stop(), PERIOD)

    P().run()
    return ST


if __name__ == "__main__":
    st = run()
    n = max(1, st["n"])
    print("")
    print("  === _sync_rects 守卫命中率 (周期 %gs) ===" % PERIOD)
    print("  遍历 %d 次 ; 实际写入 %d (%.1f%%) ; 命中(没写) %d (%.1f%%)"
          % (st["n"], st["wrote"], 100.0 * st["wrote"] / n,
             st["hold"], 100.0 * st["hold"] / n))
    s = max(1, st["still"])
    print("  已停稳的 %d 次中, **仍然被重写的 %d (%.1f%%)**"
          % (st["still"], st["still_rewritten"], 100.0 * st["still_rewritten"] / s))
    print("  ⇒ 停稳粒子被重写的比例就是这道守卫**没吃到**的收益。")
    print("")
