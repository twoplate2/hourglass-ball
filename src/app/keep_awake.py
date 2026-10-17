"""Foreground-only screen inhibition; Android uses DanZhu's window-flag pattern."""

import ctypes


class KeepAwake:
    def __init__(self, platform, window, logger):
        self.platform = platform
        self.window = window
        self.logger = logger
        self._enabled = False
        self._task = None
        self._screensaver_before = None

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        try:
            if self.platform == "android":
                from jnius import autoclass, PythonJavaClass, java_method

                if self._task is None:
                    owner = self

                    class ScreenTask(PythonJavaClass):
                        __javainterfaces__ = ["java/lang/Runnable"]

                        @java_method("()V")
                        def run(self):
                            try:
                                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                                flags = autoclass("android.view.WindowManager$LayoutParams")
                                window = activity.getWindow()
                                # Read current intent, not a stale queued start/resume value.
                                if owner._enabled:
                                    window.addFlags(flags.FLAG_KEEP_SCREEN_ON)
                                else:
                                    window.clearFlags(flags.FLAG_KEEP_SCREEN_ON)
                            except Exception:
                                owner.logger.exception("KeepAwake: Android window update failed")

                    self._task = ScreenTask()
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                activity.runOnUiThread(self._task)
            else:
                if self._enabled:
                    if self._screensaver_before is None:
                        self._screensaver_before = self.window.allow_screensaver
                    self.window.allow_screensaver = False
                elif self._screensaver_before is not None:
                    self.window.allow_screensaver = self._screensaver_before
                    self._screensaver_before = None
                if self.platform == "win":
                    # The app lifecycle runs on the same main thread that owns this request.
                    flags = 0x80000003 if self._enabled else 0x80000000
                    request = ctypes.windll.kernel32.SetThreadExecutionState
                    request.argtypes = [ctypes.c_uint]
                    request.restype = ctypes.c_uint
                    if not request(flags):
                        raise OSError("SetThreadExecutionState failed")
            return True
        except Exception:
            self.logger.exception("KeepAwake: request failed")
            return False
