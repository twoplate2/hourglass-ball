# -*- coding: utf-8 -*-
"""**批处理沙流渲染器接管时, 那次 `_reserve_stream_lines()` 是纯白做** —— 量它。

主张(2026-10-09 对抗审查 A6 / 1号 BUILD-3):
  `flow_texture_experiment.build_texture_batches` 在 `build(self)` 之后对 24 个桶
  `group.clear(); pool.clear()` ⇒ 基类预建的 `Line` **全被丢掉**, texture 路径一个读者都没有。

本探针量三件:
  ① **白做是真的**: 建完画布 `Σlen(pool)` 应为 0(texture 臂)。
  ② **省了多少**: 交替计时 `_build_dynamic_canvas()`(带预留 / 跳过预留),
     只信�交替配对差 —— 单轮绝对值会漂。
  ③ **回退口自己走得通**(负对照): `HG_FLOW_FORCE_FAIL=1` 逼编译失败 ⇒
     必须看到异常被抛出 **且** 池子被补回来(`Σlen(pool) > 0`)。
     没有这条,(1)/(2) 的"省"就可能省掉回退路径的命根子。

跑法: python tools/_probe_reserve_waste.py
"""
import os
import statistics as st
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

with tempfile.TemporaryDirectory(prefix="reserve-") as home:
    os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
    import main as m
    from kivy.clock import Clock
    from kivy.core.window import Window
    m.HourglassWidget.load_config = lambda *_: {"duration": 15.0}
    m.HourglassWidget.save_config = lambda *_: None
    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None

    class Probe(m.HourglassApp):
        def on_start(self):
            Clock.unschedule(self.hourglass.tick)
            Window.system_size = (1904, 2890)
            Clock.schedule_once(self.go, 0.6)

        def go(self, _dt):
            self.root.apply_orientation(); self.root.do_layout()
            self.root._anchor.do_layout(); self.hourglass.parent.do_layout()
            w = self.hourglass
            w.set_duration(15.0); w.reset(); w._rebuild_height_table()

            def pool_size():
                return sum(len(p) for _g, _c, p in w._stream_pools.values())

            batched = bool(getattr(w, "_flow_batches", None) is not None)
            print("")
            print("  批处理沙流渲染器在跑 :", batched)
            print("  ① 建完画布 Σlen(pool) :", pool_size(),
                  "(texture 臂应为 0 —— 说明预留确实是白做)")

            # ② 白做的那笔钱: **直接**给 `_reserve_stream_lines()` 计时。
            #    ⚠️ 别看 `_build_dynamic_canvas()` 的差 —— texture 臂走包装器时
            #    它**总是**跳过预留(标记由包装器置位), 两个"臂"是同一个东西,
            #    量出来的差是噪声。第一版就是这么量出 −0.2/+0.5ms 的假答案。
            def timed_reserve():
                t = []
                for _ in range(3):
                    for _g, _c, p in w._stream_pools.values():
                        p.clear()                     # 清空才会真的重建(否则它是 no-op)
                    t0 = time.perf_counter()
                    w._reserve_stream_lines()
                    t.append((time.perf_counter() - t0) * 1000.0)
                return t

            reserve_ms = timed_reserve()
            print("")
            print("  ② **被跳过的那笔钱**: `_reserve_stream_lines()` 单次 "
                  "%s ms(中位 %.1f)" % (["%.1f" % v for v in reserve_ms], st.median(reserve_ms)))
            print("     它建的 Line 数 = %d(这些在 texture 路径下当场被 pool.clear() 丢掉)"
                  % pool_size())
            w._build_dynamic_canvas()          # 复原(把上面手工清空的池子重建回来)

            # ③ 负对照: 逼它走回退口
            os.environ["HG_FLOW_FORCE_FAIL"] = "1"
            raised = None
            try:
                w._build_dynamic_canvas()
            except Exception as exc:                     # noqa: BLE001
                raised = str(exc)
            finally:
                os.environ.pop("HG_FLOW_FORCE_FAIL", None)
            print("")
            print("  ③ 负对照 HG_FLOW_FORCE_FAIL")
            print("     抛出了异常        :", raised)
            print("     回退后 Σlen(pool)  :", pool_size(), "(必须 > 0 —— 否则回退路径没图元)")
            print("     _stream_np_only   :", getattr(w, "_stream_np_only", None), "(必须 False)")
            self.stop()

    Probe().run()
