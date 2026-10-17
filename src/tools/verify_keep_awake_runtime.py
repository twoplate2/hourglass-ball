"""Hidden real-app lifecycle check; not an audio/FPS or Android device claim."""

import ctypes
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix="shalou-power-") as home:
        os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
        from kivy.config import Config
        Config.set("graphics", "window_state", "hidden")
        Config.set("graphics", "width", "400")
        Config.set("graphics", "height", "800")
        from kivy.core.window import Window
        from kivy.clock import Clock
        import main as game

        game.HourglassWidget._make_sound_proxy = lambda *_: None
        game.HourglassWidget._make_completion_sound = lambda *_: None
        game.HourglassWidget._load_voice_bank = lambda *_: None
        app = game.HourglassApp()
        app.root = app.build()
        widget = app.hourglass
        Clock.unschedule(widget.tick)

        def check(enabled):
            assert app._keep_awake._enabled is enabled
            assert Window.allow_screensaver is (not enabled)
            if sys.platform == "win32":
                flags = 0x80000003 if enabled else 0x80000000
                previous = ctypes.windll.kernel32.SetThreadExecutionState(flags)
                assert previous == flags, (previous, flags)

        try:
            app.on_start()
            check(True)
            assert not widget.running
            widget.running = True
            widget.tick(0.02)
            check(True)
            widget.running = False
            check(True)
            widget.elapsed = widget.duration
            check(True)
            app.on_pause()
            check(False)
            app.on_resume()
            check(True)
            if sys.platform == "win32":
                hwnd = Window.get_window_info().window
                visible = ctypes.windll.user32.IsWindowVisible
                visible.argtypes = [ctypes.c_void_p]
                visible.restype = ctypes.c_int
                assert not visible(hwnd)
            assert Config.get("graphics", "window_state") == "hidden"
        finally:
            app.on_stop()
            check(False)
            Window.close()
        print("KEEP_AWAKE_RUNTIME_OK idle+running+paused+finished+background+resume+stop")


if __name__ == "__main__":
    main()
