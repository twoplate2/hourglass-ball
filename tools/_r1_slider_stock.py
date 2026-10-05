"""Is the dev-menu cursor blue by Kivy default? Minimal stock Slider render."""
import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory() as home:
        os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1",
                          KIVY_METRICS_DENSITY="1", KIVY_METRICS_FONTSCALE="1")
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.slider import Slider
        from kivy.uix.label import Label
        from kivy.graphics import Color, Rectangle
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from PIL import Image

        class Stock(App):
            def build(self):
                root = BoxLayout(orientation="vertical", padding=40)
                with root.canvas.before:
                    Color(0.98, 0.96, 0.89, 1)
                    self.bg = Rectangle(pos=root.pos, size=root.size)
                root.bind(pos=lambda *a: setattr(self.bg, "pos", root.pos),
                          size=lambda *a: setattr(self.bg, "size", root.size))
                root.add_widget(Label(text="stock slider", color=(0.2, 0.2, 0.2, 1)))
                root.add_widget(Slider(min=0, max=100, value=35))
                return root

            def on_start(self):
                Clock.schedule_once(self.take, 1.2)

            def take(self, _dt):
                w, h = map(int, Window.size)
                px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (w, h), px)
                img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB").save(
                    ROOT / "benchmark_logs" / "_r1" / "stock_slider.png")
                import numpy as np
                a = np.asarray(img.convert("RGB")).astype(int)
                r, g, b = a[..., 0], a[..., 1], a[..., 2]
                m = (b > 150) & (b - r > 60)
                print("PROBE stock blue px:", int(m.sum()))
                if m.any():
                    ys, xs = m.nonzero()
                    from collections import Counter
                    c = Counter(tuple(a[y, x]) for y, x in zip(ys, xs))
                    print("PROBE stock cursor colours:", c.most_common(4))
                self.stop()

        Window.size = (300, 220)
        Stock().run()

main()
