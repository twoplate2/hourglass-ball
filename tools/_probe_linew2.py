"""Probe 2: measure the actual pixel width of Kivy `Line.width` in [1.0, 2.2].

Finding from probe 1 (tools/_probe_linew.py, screenshot benchmark_logs/_linew.png):
    Line.width  0.5 -> 1px   1.0 -> 1px   1.9 -> 4px   4.0 -> 8px   8.0 -> 16px
i.e. for width > 1 the rendered width is about 2x the parameter (width is a
HALF-width), and <= 1 is clamped to a 1px GL_LINES. This probe finds the exact
values that give 2px and 3px.
"""
import math
from kivy.config import Config
Config.set('graphics', 'width', '400')
Config.set('graphics', 'height', '700')
Config.set('graphics', 'resizable', '0')
from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle
from kivy.uix.widget import Widget

WS = [1.0, 1.05, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8, 2.0, 2.2]


class W(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(pos=(-400, -400), size=(1200, 1500))
            for i, w in enumerate(WS):
                y = 40 + i * 55
                Color(0, 0, 0, 1)
                Line(points=[50, y, 350, y], width=w)


class A(App):
    def build(self):
        Clock.schedule_once(self.shot, 1.2)
        return W(size=(400, 700), size_hint=(None, None))

    def shot(self, dt):
        self.root.export_to_png('benchmark_logs/_linew.png')
        print('PROBE saved')
        self.stop()


A().run()
