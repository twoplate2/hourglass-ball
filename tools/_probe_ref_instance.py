# -*- coding: utf-8 -*-
"""查 `unchanged glass and true-circle sand rendering` 为什么一直红。

现象(2026-10-05 实测):
  该断言把 `old`(参照物模块的一个 widget 实例) 和 `widget`(app 里那个) 同样地
  `_rebuild_height_table()` + `redraw()`, 再各 `export_as_image()` 逐像素比。
  把参照物换成**当前 main.py 的副本**之后它**仍然红** —— 那就不是"参照物旧",
  是**两个同样代码的实例画出来不一样**。

  `benchmark_logs/geometry_regression/idle-before.png` 里 **上球是空的**(只有玻璃),
  `idle-after.png` 里上球有沙。⇒ 差的不是像素, 是 **`old` 根本没画出沙体**。

本探针把闸门那段场景原样复刻, 打出两个实例的内部状态, 定位"沙去哪了"。

跑法: python tools/_probe_ref_instance.py
"""
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="refinst-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as widget_mod
        from kivy.clock import Clock
        from kivy.core.window import Window

        widget_mod.HourglassWidget._make_sound_proxy = lambda *_: None
        widget_mod.HourglassWidget._make_completion_sound = lambda *_: None
        widget_mod.HourglassApp.on_completed = lambda *_: None
        widget_mod.HourglassWidget.load_config = lambda *_: {"duration": 60}
        widget_mod.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        widget_mod.time = types.SimpleNamespace(perf_counter=lambda: now[0])

        class P(widget_mod.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                widget = self.hourglass
                widget.duration = 60
                widget._rebuild_height_table()

                for tag, path in (("当前 main", ROOT / "main.py"),
                                  ("8-28 旧版",
                                   ROOT.parent / "backup"
                                   / "android_main_pre_perf_20261001.py")):
                    spec = importlib.util.spec_from_file_location(
                        "refmod_" + tag.replace(" ", "_"), path)
                    ref = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(ref)
                    ref.HourglassWidget._make_sound_proxy = lambda *_: None
                    old = ref.HourglassWidget(size=widget.size, pos=widget.pos)
                    Clock.unschedule(old.tick)
                    for obj in (old, widget):
                        obj.duration = 60
                        obj._rebuild_height_table()
                        obj.elapsed = 0
                        obj.running = False
                        obj.particles = []
                        obj.splashes = []
                        obj.flares = []
                        obj.dusts = []
                    old._mound_height_px = widget._mound_height_px
                    old._raw_height_ratio = lambda _v: (
                        widget._mound_height_px() / (2 * widget._R_inner))
                    for obj in (old, widget):
                        obj.redraw()
                    self.dump(tag, old, widget)
                    old._rebuild_height_table()      # 复位, 别影响下一个
                Clock.schedule_once(lambda dt: self.stop(), 0.2)

            @staticmethod
            def dump(tag, old, widget):
                print("\n=== 参照物: %s ===" % tag)
                for name, o in (("old(游离)", old), ("widget(app 内)", widget)):
                    kids = list(o.canvas.children)
                    kinds = {}
                    for ins in kids:
                        kinds[type(ins).__name__] = kinds.get(type(ins).__name__, 0) + 1
                    print("  %-16s parent=%-5s size=%s" % (name, o.parent is not None,
                                                           tuple(round(v, 1) for v in o.size)))
                    print("     _geom_ready=%s  canvas 指令 %d 条: %s"
                          % (getattr(o, "_geom_ready", None), len(kids),
                             dict(sorted(kinds.items(), key=lambda kv: -kv[1])[:6])))
                    for attr in ("_upper_sand_bot", "_lower_sand_bot",
                                 "_upper_sand_height_px", "_R_inner"):
                        v = getattr(o, attr, None)
                        try:
                            v = round(v(), 2) if callable(v) else (
                                round(v, 2) if isinstance(v, (int, float)) else v)
                        except Exception as exc:
                            v = "<%s: %s>" % (type(exc).__name__, exc)
                        print("     %-22s = %s" % (attr, v))

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
