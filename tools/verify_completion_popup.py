"""桌面烟测: 验证完成弹窗的两条规矩, 并给弹窗截一张图看排版有没有塌。

① 周期 < `COMPLETION_POPUP_MIN`(20s) 的**不弹**(只有声音);  ② 该弹的要**延后**
`COMPLETION_POPUP_DELAY` 秒。第一段用**生产常数**跑 2 秒周期, 断言到点也不弹 ——
第二段把门槛/延迟覆写成 0, 让它真弹出来截图。

只读代码路径, 不写配置(set_duration 内部会 save_config, 所以先把配置路径改到临时目录)。
用法: python tools/verify_completion_popup.py
产物: completion_popup.png (仓库根目录); 断言失败会打印 FAIL 字样
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
        Clock.schedule_once(self._phase_short_period, 1.5)

    def _phase_short_period(self, _dt):
        """① 生产常数 + 2 秒周期 ⇒ 到点也不该弹。"""
        self.hourglass.set_duration(2)
        self.hourglass.toggle()
        print("duration:", self.hourglass.duration,
              "| popup_min:", main.COMPLETION_POPUP_MIN,
              "| delay:", main.COMPLETION_POPUP_DELAY)
        Clock.schedule_once(self._check_no_popup,
                            2 + main.COMPLETION_POPUP_DELAY + 0.4)

    def _check_no_popup(self, _dt):
        opened = self._completion_popup is not None
        print("① <20s 不弹:", "PASS" if not opened else "FAIL —— 弹出来了")
        print("   完成音还在播:", getattr(self.hourglass._completion_spoken,
                                          "backend", None))
        # ② 覆写门槛与延迟, 让它弹出来, 截一张图看排版
        main.COMPLETION_POPUP_MIN = 0.0
        main.COMPLETION_POPUP_DELAY = 0.3
        self.hourglass.set_duration(2)
        self.hourglass.toggle()
        Clock.schedule_once(self._shot, 2 + 0.3 + 1.0)

    def _shot(self, _dt):
        opened = self._completion_popup is not None
        print("② 覆写门槛后弹窗 open:", "PASS" if opened else "FAIL —— 没弹",
              "| size:", getattr(self._completion_popup, "size", None))
        print("   spoken backend:", getattr(self.hourglass._completion_spoken, "backend", None))
        print("   voice keys:", " ".join(self.hourglass._voice_bank.sentence_keys(2)))
        Window.screenshot(name="completion_popup.png")
        Clock.schedule_once(lambda _d: self.stop(), 0.8)


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="hg_popup_")
    main.config_path = lambda: os.path.join(tmp, ".hourglass_config.json")
    TestApp().run()
