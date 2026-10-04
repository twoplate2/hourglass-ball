"""Probe: does the glass highlight sit BELOW the pause/flash overlay?

meishu2.md sec 5.2 asks for three draw positions: glass shell (before sand),
transparent highlights (after sand and particles), and the UI layer last. If the
overlay is drawn on top of the highlights, the pause veil (BG_COLOR at 55%) must
COMPRESS the highlight-vs-no-highlight difference to ~45%. If the highlight were
inserted after the overlay, the difference would be unchanged.

Renders the same frame twice (highlight on / off) in one process by flipping the
module constant and rebuilding the group, so the comparison is paired and the
particles are identical.
"""
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / "benchmark_logs" / "flow_visual_hl_layers"
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hl-layers-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from PIL import Image
        import main as module

        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": 60}
        module.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])

        class ProbeApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.begin, 1.0)

            def begin(self, _dt):
                Window.size = (400, 800)
                Clock.schedule_once(self.setup, 0.4)

            def setup(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                w.set_duration(5.0)
                w.completion_enabled = False
                w.toggle()
                real = w.redraw
                w.redraw = lambda: None
                while w.elapsed < 2.5:
                    now[0] += 1 / 120
                    w.tick(1 / 120)
                w.redraw = real
                self.real_draw = real
                # ⚠️ glReadPixels 读的是**当前 framebuffer**, 而 Kivy 的真正绘制发生在
                # 下一帧的 on_draw —— 在同一个 Clock 回调里 redraw()+grab 只会拿到上一帧
                # (第一版就是这么写的, 三态全部 diffpx=0)。每一步之间必须隔开一帧。
                self.queue = [("running", True), ("running", False),
                              ("paused", True), ("paused", False),
                              ("flash", True), ("flash", False)]
                self.next_shot(0)

            def grab(self, name):
                width, height = map(int, Window.size)
                px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (width, height), px)
                img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB").save(
                    out / name)

            def next_shot(self, _dt):
                if not self.queue:
                    print("PROBE done")
                    self.stop()
                    return
                tag, flag = self.queue.pop(0)
                w = self.hourglass
                if tag == "running":
                    w.running = True
                elif tag == "paused":
                    w.running = False            # -> pause veil alpha 0.55
                elif tag == "flash":
                    w.running = True
                    w.flash_end = now[0] + 0.35  # -> white 25% overlay
                module.GLASS_HL_ENABLE = flag
                w._rebuild_glass_highlights()
                w.redraw()
                Clock.schedule_once(lambda _dt: self.take(tag, flag), 0.08)

            def take(self, tag, flag):
                self.grab("%s_%s.png" % (tag, "hl" if flag else "nohl"))
                Clock.schedule_once(self.next_shot, 0.08)

        ProbeApp().run()


main()
