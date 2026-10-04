"""一次性探针: 量 Kivy `Line` 的 width 语义(生产代码的 1.9px 实拍成了 5px)。"""
import math
from kivy.config import Config
Config.set('graphics', 'width', '400'); Config.set('graphics', 'height', '300')
Config.set('graphics', 'resizable', '0')
from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle
from kivy.uix.widget import Widget


def arc(cx, cy, r, d0, d1, segs):
    out = []; a0 = math.radians(d0); span = math.radians(d1 - d0)
    for i in range(segs + 1):
        a = a0 + span * i / segs
        out += [cx + r * math.cos(a), cy + r * math.sin(a)]
    return out


class W(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)
        print('PROBE widget size', self.size, 'density', self.density if hasattr(self,'density') else '?')
        with self.canvas:
            Color(1, 1, 1, 1); Rectangle(pos=(-200, -200), size=(900, 900))
            for y, w in ((60, 1.0), (110, 1.9), (160, 4.0), (210, 8.0), (250, 0.5)):
                Color(0, 0, 0, 1); Line(points=[50, y, 350, y], width=w)
            Color(1, 0, 0, 1)
            Line(points=arc(200, 150, 60, 100, 170, 4), width=1.9, cap='round')


class A(App):
    def build(self):
        Clock.schedule_once(self.shot, 1.2)
        return W(size=(400, 300), size_hint=(None, None))

    def shot(self, dt):
        self.root.export_to_png('benchmark_logs/_linew.png')
        print('PROBE saved')
        self.stop()


A().run()
