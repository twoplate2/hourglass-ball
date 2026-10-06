# -*- coding: utf-8 -*-
"""飞溅改前/改后 **下球**并排图 —— `inspect_flow.py` 只出颈部裁图, 看不到沙堆。

用户口径(项目红线): 「改任何视觉之前, 先把改前/改后的**并排图**交回用户;
统计量只允许事后解释'为什么', 不许事后当'好不好'的判据。」
本脚本只生产并排图, **不下任何结论**。

⚠️ 两臂的随机数流**必然不同**(改动本身改变了调用序列, 代码注释已写明) ⇒
   本图只能用于**观感判断**, 不能用于逐像素断言。

跑法:
  python tools/_ab_mound.py <source_main.py> <label> [周期] [抓几个时刻]
产物: _shot/r37/ab/<label>_mound_t<时刻>.png
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_shot" / "r37" / "ab"


def main():
    src = Path(sys.argv[1]).resolve()
    label = sys.argv[2]
    period = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
    shots = [float(v) for v in (sys.argv[4].split(",") if len(sys.argv) > 4
                                else ["4.0", "6.0", "8.0", "10.0"])]
    # ⚠️ **不要硬设 KIVY_METRICS_DENSITY** —— 本脚本要判的是**桌面真实布局**,
    #    硬设 1.75(设备模拟)会让 400px 宽的窗上所有 dp/sp 全放大 1.75 倍、
    #    底栏与色块行**必然溢出**, 那是量具造出来的假象(2026-10-06 踩过:
    #    用户报"界面出问题了", 我先在自己抓的图上看见同样的溢出, 差点当成真 bug)。
    #    要复现设备的密度, 请**显式**在环境变量里给。
    if os.environ.get("KIVY_METRICS_DENSITY"):
        print("  [密度] 按环境变量 KIVY_METRICS_DENSITY=%s"
              % os.environ["KIVY_METRICS_DENSITY"])
    else:
        print("  [密度] 用 Kivy 原生密度(桌面真实条件)")
    os.environ.setdefault("KIVY_NO_ARGS", "1")
    sys.argv = [sys.argv[0], "_ab_mound"]
    sys.path.insert(0, str(src.parent))      # main.py 里的 `from app_version import ...`
    sys.path.insert(0, str(ROOT))            # 兜底: 仓库根的 app_version / tools 模块
    # 让 import 到的是**指定那份** main.py(两臂共用同一个进程外的 tools/)
    import importlib.util
    spec = importlib.util.spec_from_file_location("main_ab", src)
    m = importlib.util.module_from_spec(spec)
    sys.modules["main_ab"] = m
    spec.loader.exec_module(m)

    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
    from PIL import Image
    import numpy as np
    import random

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {"duration": period}
    m.HourglassWidget.save_config = lambda *_: None
    random.seed(23)                       # 两臂同种子(但调用序列仍不同, 见文件头)

    OUT.mkdir(parents=True, exist_ok=True)
    pending = list(shots)
    done = []

    def grab(tag):
        w, h = int(Window.width), int(Window.height)
        px = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
        img = np.asarray(Image.frombytes("RGBA", (w, h), px)
                         .transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB"))
        Image.fromarray(img).save(OUT / ("%s_mound_t%04.1f.png" % (label, tag)))
        done.append(tag)

    class P(m.HourglassApp):
        def on_start(self):
            _ws = os.environ.get("HG_WSIZE", "400x800").split("x")
            Window.size = (int(_ws[0]), int(_ws[1]))
            Clock.schedule_once(self.go, 1.2)

        def go(self, _dt):
            self.hourglass.duration = period
            self.hourglass._rebuild_height_table()
            self.hourglass.toggle()

            def tick(*_a):
                hg = self.hourglass
                while pending and hg.elapsed >= pending[0]:
                    grab(pending.pop(0))
                if not pending or not hg.running:
                    Clock.unschedule(tick)
                    Clock.schedule_once(lambda dt: self.stop(), 0.2)

            Clock.schedule_interval(tick, 1.0 / 60.0)

    P().run()
    print("  %-8s 抓到 %d 张: %s" % (label, len(done), done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
