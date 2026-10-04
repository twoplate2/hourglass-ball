"""Self-check for the two dev-menu sliders (sand grain + glass highlight).

Covers the things that actually break in this kind of change:
  1. the menu opens and exposes both sliders;
  2. dragging the sand slider swaps in a 128px PREVIEW material (fast) and does
     NOT rebuild the canvas (that would recreate ~2500 particle lines per frame);
  3. after 0.35s of no input it bakes the real 512px material and persists it;
  4. changing sand colour drops a stale preview;
  5. the glass slider drives GLASS_HL_MUL and rebuilds the highlight group, and
     the value survives a save/load round trip.

NOTE: deliberately does NOT stub module.time -- the preview throttle in on_sand
compares real perf_counter() values, and a frozen clock would silently disable it.
"""
from pathlib import Path
import json
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="dev-menu-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        import main as module

        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        # ⚠️ **必须**把配置路径指到临时目录。桌面版 config_path() 用的是
        # `~/.hourglass_config.json`(与 pc 版共享), 直接跑会**静默改写用户的真实配置**
        # —— 第一次跑就把它写成了 duration=5/红沙/grain 0.63/glass_hl 2.5。
        # 而且第二次跑还会因为"滑块初值已等于要设的值"导致回调不触发、测试假失败。
        probe_cfg = Path(home) / "hourglass_config.json"
        module.config_path = lambda: str(probe_cfg)

        results = []

        def check(name, ok, detail=""):
            results.append((name, ok, detail))
            print("CHECK %-46s %s %s" % (name, "PASS" if ok else "**FAIL**", detail))

        class ProbeApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.setup, 1.0)

            def setup(self, _dt):
                Window.size = (400, 800)
                Clock.schedule_once(self.run_test, 0.4)

            def run_test(self, _dt):
                w = self.hourglass
                w.set_duration(5.0)
                w.toggle()
                Clock.schedule_once(self.test_menu, 0.6)

            def test_menu(self, _dt):
                w = self.hourglass
                self._open_dev_menu()
                Clock.schedule_once(self.grab_menu, 0.40)

            def grab_menu(self, _dt):
                from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
                from PIL import Image
                width, height = map(int, Window.size)
                px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (width, height), px)
                out = ROOT / "benchmark_logs" / "flow_visual_dev_menu"
                out.mkdir(exist_ok=True)
                img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB").save(
                    out / "menu_open.png")
                Clock.schedule_once(self.test_sand, 0.15)

            def test_sand(self, _dt):
                w = self.hourglass
                check("菜单打开", self._dev_popup is not None)
                sliders = getattr(self, "_dev_sliders", None)
                check("暴露两个滑块", sliders is not None and len(sliders) == 2,
                      str(sliders is not None and len(sliders)))
                if not sliders:
                    self.finish(); return
                sand, glass = sliders
                self.sand_slider, self.glass_slider = sand, glass
                # 记录画布身份: 拖动期间它**不能**被重建
                self.canvas_id_before = id(w._stream_pools[(0, 2)][0])
                self.pool_len_before = len(w._stream_pools[(0, 2)][2])
                sand.value = 90.0                      # 拖到 0.63
                Clock.schedule_once(self.check_preview, 0.10)

            def check_preview(self, _dt):
                w = self.hourglass
                mat = w._preview_material
                check("拖动中挂上预览材质", mat is not None)
                if mat is not None:
                    check("预览是低分辨率(128)",
                          tuple(mat.texture.size) == (module.SAND_PREVIEW_SIZE,) * 2,
                          str(tuple(mat.texture.size)))
                check("拖动**没有**重建画布(粒子池对象不变)",
                      id(w._stream_pools[(0, 2)][0]) == self.canvas_id_before
                      and len(w._stream_pools[(0, 2)][2]) == self.pool_len_before)
                check("沙体 rect 已换成预览纹理",
                      w._sand_chords[0][1].texture is (mat.texture if mat else None))
                Clock.schedule_once(self.check_commit, 0.6)

            def check_commit(self, _dt):
                w = self.hourglass
                check("停手后预览槽已清空", w._preview_material is None)
                check("正式材质是 512",
                      w._sand_material is not None
                      and tuple(w._sand_material.texture.size) == (module.SAND_MATERIAL_SIZE,) * 2,
                      str(tuple(w._sand_material.texture.size)) if w._sand_material else "-")
                check("grain 写进全局", abs(module.SAND_MATERIAL_GRAIN - 0.63) < 0.005,
                      "%.3f" % module.SAND_MATERIAL_GRAIN)
                try:
                    cfg = json.load(open(module.config_path(), encoding="utf-8"))
                except Exception:
                    cfg = {}
                check("配置已落盘 grain", abs(cfg.get("sand_grain", -1) - 0.63) < 0.005,
                      str(cfg.get("sand_grain")))
                self.glass_slider.value = 100.0
                Clock.schedule_once(self.check_glass, 0.6)

            def check_glass(self, _dt):
                w = self.hourglass
                check("玻璃反光倍数已改", abs(module.GLASS_HL_MUL - module.GLASS_HL_MAX) < 1e-6,
                      "%.2f" % module.GLASS_HL_MUL)
                check("反光组已重建(非空)", len(w._glass_hl_group.children) > 0,
                      "%d 条指令" % len(w._glass_hl_group.children))
                try:
                    cfg = json.load(open(module.config_path(), encoding="utf-8"))
                except Exception:
                    cfg = {}
                check("配置已落盘 glass_hl",
                      abs(cfg.get("glass_hl", -1) - module.GLASS_HL_MAX) < 1e-6,
                      str(cfg.get("glass_hl")))
                # 切色必须丢掉陈旧预览
                w._preview_material = "STALE"
                n, b, d, l = module.SAND_PRESETS[1]
                self.on_color(b, d, l, n)
                check("切色丢弃陈旧预览", w._preview_material is None)
                check("切色后沙体纹理是当前配色的材质",
                      w._sand_chords[0][1].texture is w._sand_material.texture)
                Clock.schedule_once(self.finish, 0.2)

            def finish(self, _dt=None):
                bad = [r for r in results if not r[1]]
                print("CHECK ==== %d/%d 通过 ====" % (len(results) - len(bad), len(results)))
                for name, _ok, detail in bad:
                    print("CHECK FAILED: %s  %s" % (name, detail))
                self.stop()

        ProbeApp().run()


main()
