"""Windowless regression tests for foreground screen/sleep inhibition."""

import ast
import ctypes
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from app.keep_awake import KeepAwake
from check_rollback_release import normalized_main


class KeepAwakeTests(unittest.TestCase):
    def setUp(self):
        self.window = SimpleNamespace(allow_screensaver=True)
        self.logger = Mock()

    def controller(self, platform):
        return KeepAwake(platform, self.window, self.logger)

    def android(self):
        window = Mock()
        queue = []
        activity = SimpleNamespace(getWindow=lambda: window, runOnUiThread=queue.append)
        classes = {
            "org.kivy.android.PythonActivity": SimpleNamespace(mActivity=activity),
            "android.view.WindowManager$LayoutParams": SimpleNamespace(FLAG_KEEP_SCREEN_ON=128),
        }
        jnius = SimpleNamespace(PythonJavaClass=object,
                                java_method=lambda *_: lambda method: method,
                                autoclass=lambda name: classes[name])
        return window, queue, jnius

    def test_android_posts_to_ui_thread_and_uses_nested_constant(self):
        window, queue, jnius = self.android()
        with patch.dict(sys.modules, jnius=jnius):
            controller = self.controller("android")
            self.assertTrue(controller.set_enabled(True))
            window.addFlags.assert_not_called()
            queue.pop().run()
            window.addFlags.assert_called_once_with(128)
            self.assertTrue(controller.set_enabled(False))
            queue.pop().run()
            window.clearFlags.assert_called_once_with(128)

    def test_queued_start_cannot_reenable_after_stop(self):
        window, queue, jnius = self.android()
        with patch.dict(sys.modules, jnius=jnius):
            controller = self.controller("android")
            controller.set_enabled(True)
            controller.set_enabled(False)
            for task in reversed(queue):
                task.run()
            window.addFlags.assert_not_called()
            self.assertEqual(window.clearFlags.call_count, 2)

    def test_resume_reapplies_flag(self):
        window, queue, jnius = self.android()
        with patch.dict(sys.modules, jnius=jnius):
            controller = self.controller("android")
            for enabled in (True, False, True):
                controller.set_enabled(enabled)
                queue.pop().run()
            self.assertEqual(window.addFlags.call_count, 2)

    def test_android_failure_is_logged(self):
        with patch.dict(sys.modules, jnius=None):
            self.assertFalse(self.controller("android").set_enabled(True))
        self.logger.exception.assert_called_once()

    def test_android_ui_failure_is_logged(self):
        window, queue, jnius = self.android()
        window.addFlags.side_effect = RuntimeError("window unavailable")
        with patch.dict(sys.modules, jnius=jnius):
            self.controller("android").set_enabled(True)
            queue.pop().run()
        self.logger.exception.assert_called_once()

    def test_windows_blocks_display_and_system_idle_then_releases(self):
        native = Mock(return_value=1)
        with patch.object(ctypes, "windll", SimpleNamespace(
                kernel32=SimpleNamespace(SetThreadExecutionState=native)), create=True):
            controller = self.controller("win")
            self.assertTrue(controller.set_enabled(True))
            self.assertFalse(self.window.allow_screensaver)
            self.assertTrue(controller.set_enabled(False))
            self.assertTrue(self.window.allow_screensaver)
        self.assertEqual([call.args[0] for call in native.call_args_list],
                         [0x80000003, 0x80000000])

    def test_windows_failed_request_is_not_success(self):
        with patch.object(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(
                SetThreadExecutionState=Mock(return_value=0))), create=True):
            self.assertFalse(self.controller("win").set_enabled(True))
        self.logger.exception.assert_called_once()

    def test_preserves_existing_screensaver_setting(self):
        self.window.allow_screensaver = False
        controller = self.controller("linux")
        controller.set_enabled(True)
        controller.set_enabled(False)
        self.assertFalse(self.window.allow_screensaver)

    def test_foreground_lifecycle_includes_idle_pause_and_finish(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        app = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == "HourglassApp")
        methods = {node.name: node for node in app.body if isinstance(node, ast.FunctionDef)}
        for method, enabled in (("on_start", True), ("on_resume", True),
                                ("on_pause", False), ("on_stop", False)):
            statement = methods[method].body[0]
            self.assertIsInstance(statement, ast.Expr)
            call = statement.value
            self.assertEqual(ast.unparse(call.func), "self._set_keep_awake")
            self.assertIs(ast.literal_eval(call.args[0]), enabled)
        # Screen policy is independent of timer/benchmark state.
        helper = methods["_set_keep_awake"]
        self.assertNotIn("running", ast.unparse(helper))
        self.assertNotIn("_benchmark_active", ast.unparse(helper))

    def test_rollback_allows_only_lifecycle_power_hooks(self):
        before = "class HourglassApp:\n def on_start(self):\n  self.work()\n"
        after = ("from app.keep_awake import KeepAwake\nclass HourglassApp:\n"
                 " def _set_keep_awake(self, enabled):\n  pass\n"
                 " def on_start(self):\n  self._set_keep_awake(True)\n  self.work()\n")
        self.assertEqual(normalized_main(before), normalized_main(after))

    def test_rollback_rejects_physics_changes(self):
        before = "gravity = 450\nclass HourglassApp:\n pass\n"
        after = before.replace("450", "400")
        self.assertNotEqual(normalized_main(before), normalized_main(after))

    def test_rollback_rejects_pymunk_import(self):
        before = "class HourglassApp:\n pass\n"
        self.assertNotEqual(normalized_main(before),
                            normalized_main("from app.pymunk_sand import update_widget\n" + before))


if __name__ == "__main__":
    unittest.main()
