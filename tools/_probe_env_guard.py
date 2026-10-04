"""Verify the env-first guard still wins over config for the two new settings.

`HG_SAND_MATERIAL` / `HG_SAND_GRAIN` / `HG_GLASS_HL` / `HG_GLASS_HL_MUL` are the
switches used by the capture and A/B tooling. If a local config file could
override them, every measurement would silently run the wrong setting (and both
A/B arms would collapse into the same version). That hole was found once before;
this checks it is still closed after the slider rewrite.
"""
from pathlib import Path
import json
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="env-guard-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        # 配置里写一组**故意相反**的值, env 必须压过它
        cfg = Path(home) / "hourglass_config.json"
        json.dump({"duration": 10.0, "color_name": "金沙", "sound_name": "无声音",
                   "sand_mode": "grain", "sand_grain": 0.10, "glass_hl": 0.10},
                  open(cfg, "w", encoding="utf-8"), ensure_ascii=False)
        os.environ["HG_SAND_GRAIN"] = "0.70"
        os.environ["HG_GLASS_HL_MUL"] = "2.00"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App
        from kivy.clock import Clock
        import main as module

        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.config_path = lambda: str(cfg)

        results = []

        def check(name, ok, detail=""):
            results.append(ok)
            print("CHECK %-42s %s %s" % (name, "PASS" if ok else "**FAIL**", detail))

        class ProbeApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.verify, 1.0)

            def verify(self, _dt):
                check("HG_SAND_GRAIN 压过配置(0.10)",
                      abs(module.SAND_MATERIAL_GRAIN - 0.70) < 1e-6,
                      "%.3f" % module.SAND_MATERIAL_GRAIN)
                check("HG_GLASS_HL_MUL 压过配置(0.10)",
                      abs(module.GLASS_HL_MUL - 2.00) < 1e-6,
                      "%.2f" % module.GLASS_HL_MUL)
                print("CHECK ==== %d/%d ====" % (sum(results), len(results)))
                self.stop()

        ProbeApp().run()


main()
