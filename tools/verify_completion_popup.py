"""桌面烟测: 跑一次 2 秒周期, 在流尽后给弹窗截图, 验证弹窗真的出现且排版没塌。

只读代码路径, 不写配置(set_duration 内部会 save_config, 所以先把配置路径改到临时目录)。
用法: python tools/verify_completion_popup.py
产物: completion_popup.png (仓库根目录)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.window import Window  # noqa: E402

Window.size = (400, 875)


class TestApp(main.HourglassApp):
    def on_start(self):
        super().on_start()
        Clock.schedule_once(self._arm, 1.5)

    def _arm(self, _dt):
        self.hourglass.set_duration(2)
        self.hourglass.toggle()
        print("running:", self.hourglass.running, "duration:", self.hourglass.duration)
        Clock.schedule_once(self._shot, 3.0)

    def _shot(self, _dt):
        print("popup open:", self._completion_popup is not None,
              "| size:", getattr(self._completion_popup, "size", None))
        print("spoken backend:", getattr(self.hourglass._completion_spoken, "backend", None))
        print("keys:", " ".join(self.hourglass._voice_bank.sentence_keys(2)))
        Window.screenshot(name="completion_popup.png")
        Clock.schedule_once(lambda _d: self.stop(), 0.8)


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="hg_popup_")
    main.config_path = lambda: os.path.join(tmp, ".hourglass_config.json")
    TestApp().run()
