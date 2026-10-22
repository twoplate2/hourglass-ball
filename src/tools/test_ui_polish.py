"""Windowless regression for timer labels and the paused settings preview.

Execute the actual application methods against small UI/event stubs. This does
not start Kivy or replace the separate on-device appearance review.
"""

import ast
from copy import deepcopy
from pathlib import Path
from types import MethodType, SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
TREE = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
CLASSES = {node.name: node for node in TREE.body if isinstance(node, ast.ClassDef)}


def method(class_name, name, globals_=None):
    node = next((node for node in CLASSES[class_name].body
                 if isinstance(node, ast.FunctionDef) and node.name == name), None)
    if node is None:
        raise AssertionError("Missing application method: %s.%s" % (class_name, name))
    namespace = {} if globals_ is None else dict(globals_)
    exec(compile(ast.Module(body=[deepcopy(node)], type_ignores=[]),
                 str(ROOT / "main.py"), "exec"), namespace)
    return namespace[name]


class UIStub:
    instances = []

    def __init__(self, **kwargs):
        self.children = []
        self.events = {}
        self.text = ""
        self.height = 0
        self.padding = [0, 0, 0, 0]
        self.spacing = 0
        self.opened = False
        self.__dict__.update(kwargs)
        self.instances.append(self)

    def add_widget(self, child):
        self.children.append(child)

    def bind(self, **events):
        for name, callback in events.items():
            self.events.setdefault(name, []).append(callback)

    def setter(self, name):
        return lambda _widget, value: setattr(self, name, value)

    def trigger(self, name, *args):
        return [callback(self, *args) for callback in self.events.get(name, [])]

    def open(self):
        self.opened = True
        self.trigger("on_open")

    def dismiss(self):
        results = self.trigger("on_dismiss")
        if any(value is True for value in results):
            raise AssertionError("Dismiss callback must not cancel closing")
        self.opened = False


class ClockStub:
    def __init__(self):
        self.pending = []

    def schedule_once(self, callback, _delay):
        self.pending.append(callback)

    def unschedule(self, callback):
        self.pending = [item for item in self.pending if item != callback]

    def flush(self):
        callbacks, self.pending = self.pending, []
        for callback in callbacks:
            callback(0)


def hourglass(running=False, elapsed=20.0):
    hg = SimpleNamespace(running=running, elapsed=elapsed, duration=50.0,
                         size=(1080, 1848), _settings_preview=False,
                         _pause_color=SimpleNamespace(a=0.55),
                         _pause_rect=SimpleNamespace(size=(1080, 1848)),
                         particles=[{"x": 1}], splashes=[{"x": 2}],
                         save_config=Mock(), set_sand_grain=Mock(return_value=True))
    for name in ("set_settings_preview", "_update_pause_overlay"):
        setattr(hg, name, MethodType(method("HourglassWidget", name), hg))
    return hg


class UIPolishTests(unittest.TestCase):
    def state_label(self, elapsed, running=False, benchmark=False):
        app = SimpleNamespace(hourglass=SimpleNamespace(
            duration=50.0, elapsed=elapsed, running=running),
            start_btn=SimpleNamespace(text="stale"), _benchmark_active=lambda: benchmark)
        method("HourglassApp", "on_run_state_changed", {"POPUP_CONFIRM": (1, 0, 0, 1)})(app)
        return app.start_btn.text

    def test_initial_button_starts(self):
        self.assertEqual(self.state_label(0), "开始")

    def test_running_button_pauses(self):
        self.assertEqual(self.state_label(20, running=True), "暂停")

    def test_paused_button_continues(self):
        self.assertEqual(self.state_label(20), "继续")

    def test_finished_button_restarts(self):
        for elapsed in (50, 50.1):
            with self.subTest(elapsed=elapsed):
                self.assertEqual(self.state_label(elapsed), "重新开始")

    def test_benchmark_button_still_cancels(self):
        self.assertEqual(self.state_label(20, running=True, benchmark=True), "取消")

    def test_preview_reveals_paused_sand_without_advancing_it(self):
        hg = hourglass()
        before = deepcopy((hg.running, hg.elapsed, hg.particles, hg.splashes))
        hg.set_settings_preview(True)
        self.assertEqual(hg._pause_color.a, 0)
        self.assertEqual(hg._pause_rect.size, (0, 0))
        self.assertEqual((hg.running, hg.elapsed, hg.particles, hg.splashes), before)
        hg.set_settings_preview(False)
        self.assertEqual(hg._pause_color.a, 0.55)
        self.assertEqual(hg._pause_rect.size, hg.size)
        self.assertEqual((hg.running, hg.elapsed, hg.particles, hg.splashes), before)

    def test_preview_close_does_not_dim_running_initial_or_finished_states(self):
        for elapsed, running in ((0, False), (20, True), (50, False)):
            with self.subTest(elapsed=elapsed, running=running):
                hg = hourglass(running, elapsed)
                hg.set_settings_preview(True)
                hg.set_settings_preview(False)
                self.assertEqual(hg._pause_color.a, 0)
                self.assertEqual(hg._pause_rect.size, (0, 0))

    def test_preview_stays_undimmed_on_subsequent_overlay_updates(self):
        hg = hourglass()
        hg.set_settings_preview(True)
        for size in ((1080, 1848), (1848, 1080), (1080, 1848)):
            with self.subTest(size=size):
                hg.size = size
                hg._update_pause_overlay()
                self.assertEqual(hg._pause_color.a, 0)
                self.assertEqual(hg._pause_rect.size, (0, 0))
                self.assertFalse(hg.running)
                self.assertEqual(hg.elapsed, 20)
        hg.set_settings_preview(False)
        self.assertEqual(hg._pause_color.a, 0.55)
        self.assertEqual(hg._pause_rect.size, hg.size)

    def open_settings(self, open_now=True):
        UIStub.instances = []
        clock = ClockStub()
        env = {name: UIStub for name in (
            "BoxLayout", "Label", "Slider", "_SmoothSlider", "Button", "Widget", "ScrollView", "_SandBgPopup")}
        env.update({name: (0.5, 0.4, 0.3, 1) for name in (
            "POPUP_TEXT", "POPUP_GOLD_SEL", "POPUP_UNSEL_MULT", "POPUP_CONFIRM",
            "POPUP_TEXT_WHITE", "POPUP_CANCEL_BG")})
        env.update(dp=lambda value: value, sp=lambda value: value,
                   Window=SimpleNamespace(width=1080, height=1848), Clock=clock,
                   APP_VERSION="test", time=time, resource_path=lambda value: value,
                   SAND_MATERIAL_GRAIN=0.35, SAND_GRAIN_MAX=0.70,
                   SAND_GRAIN_LEVELS=[("3", 1, 1.8)], SURFACE_ROUGH_LEVELS=[("3", 0.004)],
                   current_grain_level=lambda: "3", current_rough_level=lambda: "3",
                   _grain_level=lambda _value: (1, 1.8), _rough_level=lambda _value: 0.004)
        hg = hourglass()
        app = SimpleNamespace(_dev_popup=None, _benchmark_active=lambda: False,
                              hourglass=hg, _selected_color_name=lambda: "金沙",
                              on_benchmark=Mock())
        app._close_dev_menu = MethodType(method("HourglassApp", "_close_dev_menu"), app)
        app._open_dev_menu = MethodType(method("HourglassApp", "_open_dev_menu", env), app)
        if open_now:
            app._open_dev_menu()
        return app, hg, clock

    def test_settings_confirm_restores_pause_preview_and_reopens_cleanly(self):
        app, hg, _clock = self.open_settings()
        self.assertTrue(app._dev_popup.opened)
        self.assertEqual(app._dev_popup.overlay_color, (0, 0, 0, 0))
        self.assertEqual(hg._pause_color.a, 0)
        confirm = next(widget for widget in UIStub.instances if widget.text == "确定")
        confirm.trigger("on_press")
        self.assertIsNone(app._dev_popup)
        self.assertEqual(hg._pause_color.a, 0.55)
        app._open_dev_menu()
        self.assertEqual(hg._pause_color.a, 0)
        app._dev_popup.dismiss()
        self.assertEqual(hg._pause_color.a, 0.55)

    def test_settings_dismiss_back_path_restores_pause_without_saving(self):
        app, hg, _clock = self.open_settings()
        app._dev_popup.dismiss()  # Android back shares the popup on_dismiss path.
        self.assertIsNone(app._dev_popup)
        self.assertFalse(hg._settings_preview)
        self.assertEqual(hg._pause_color.a, 0.55)
        hg.save_config.assert_not_called()
        self.assertEqual(hg.elapsed, 20)
        self.assertFalse(hg.running)

    def test_popup_open_failure_restores_pause_and_allows_retry(self):
        app, hg, _clock = self.open_settings(open_now=False)
        before = deepcopy((hg.running, hg.elapsed, hg.particles, hg.splashes))
        with patch.object(UIStub, "open", side_effect=RuntimeError("popup open failed")):
            with self.assertRaisesRegex(RuntimeError, "popup open failed"):
                app._open_dev_menu()
        self.assertIsNone(app._dev_popup)
        self.assertFalse(hg._settings_preview)
        self.assertEqual(hg._pause_color.a, 0.55)
        self.assertEqual(hg._pause_rect.size, hg.size)
        self.assertEqual((hg.running, hg.elapsed, hg.particles, hg.splashes), before)
        app._open_dev_menu()
        self.assertTrue(app._dev_popup.opened)
        self.assertEqual(hg._pause_color.a, 0)
        app._dev_popup.dismiss()
        self.assertEqual(hg._pause_color.a, 0.55)

    def test_renamed_texture_slider_preserves_zero_middle_and_max_mapping(self):
        app, hg, clock = self.open_settings()
        labels = [widget.text for widget in UIStub.instances]
        self.assertIn("沙纹浓淡", labels)
        self.assertNotIn("沙子浓度", labels)
        slider = app._dev_sliders[0]
        self.assertEqual((slider.min, slider.max, slider.value), (0, 100, 50))
        for value, expected in ((0, 0), (50, 0.35), (100, 0.70)):
            with self.subTest(value=value):
                slider.trigger("value", value)
                clock.flush()
                self.assertAlmostEqual(hg.set_sand_grain.call_args.args[0], expected)
                self.assertEqual(hg.set_sand_grain.call_args.kwargs, {})


if __name__ == "__main__":
    unittest.main()
