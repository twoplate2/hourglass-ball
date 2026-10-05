# -*- coding: utf-8 -*-
"""隐藏菜单两个六档(沙体颗粒 / 沙面起伏)的验机脚本。

## 验什么
  ① 隐藏菜单里**真的有**两行各 6 个按钮, 且出厂高亮 = D / D
  ② 点某一档 ⇒ 全局真的变了(A/B 两臂必须不同, 否则是"假开关")
  ③ 点**同一档两次** = 幂等(第二次返回 False, 不该重烘材质)
  ④ 重开弹窗 ⇒ 高亮跟着走(反查标签对不对)
  ⑤ 落盘里存的是**标签**而不是数值
  ⑥ 出厂默认的材质与"用户挑的那张对照图"**逐字节相同**

⚠️ 项目红线: **跑测试脚本绝不能改写 `~/.hourglass_config.json`**。
   本脚本把 `config_path()` 改道到临时目录, 跑完即删。

跑法: python tools/verify_dev_levels.py
"""
import hashlib
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    tmp_home = tempfile.TemporaryDirectory(prefix="devlv-home-")
    tmp_cfg = tempfile.TemporaryDirectory(prefix="devlv-cfg-")
    os.environ["KIVY_HOME"] = tmp_home.name
    os.environ["KIVY_NO_ARGS"] = "1"
    os.environ["KIVY_NO_FILELOG"] = "1"
    os.environ["KIVY_METRICS_DENSITY"] = "1"
    sys.path.insert(0, str(ROOT))
    sys.argv = ["x"]
    import main as m
    from kivy.clock import Clock

    m.HourglassWidget._make_sound_proxy = lambda *_: None
    m.HourglassWidget._make_completion_sound = lambda *_: None
    m.HourglassApp.on_completed = lambda *_: None
    m.HourglassWidget.load_config = lambda *_: {}
    # 🔴 改道: 绝不碰真实的 ~/.hourglass_config.json
    cfg_file = Path(tmp_cfg.name) / "cfg.json"
    m.config_path = lambda: str(cfg_file)

    # 对照图那一版(D 档)
    spec = importlib.util.spec_from_file_location("ab", str(ROOT / "tools" / "sand_material_ab.py"))
    ab = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(ab)
    except SystemExit:
        pass

    base, dark, light = (m.hex_rgb(m.SAND_PRESETS[0][1]), m.hex_rgb(m.SAND_PRESETS[0][2]),
                         m.hex_rgb(m.SAND_PRESETS[0][3]))
    rows = []

    def chk(ok, label):
        rows.append((bool(ok), label))
        print("  %s %s" % ("PASS" if ok else "FAIL", label))

    class P(m.HourglassApp):
        def on_start(self):
            Clock.schedule_once(self.run_all, 1.0)

        def run_all(self, _dt):
            hg = self.hourglass
            print("\n① 出厂默认 = 用户挑的那一档")
            # ⚠️ 跟着**当前默认档**走, 不写死档号 —— 用户改默认值时不用改测试
            dg, dc = m._grain_level(m.SAND_GRAIN_LEVEL_DEFAULT)
            got = m._sand_material_rgba(512, base, dark, light, grain=0.35,
                                        coarse=dc, grad=dg)
            want = ab.variant_rgba(512, base, dark, light, grain=0.35, grad=dg, coarse=dc)
            chk(got == want, "沙体颗粒默认(%s 档)材质 与 对照图 逐字节相同"
                % m.SAND_GRAIN_LEVEL_DEFAULT)
            chk(abs(m.UPPER_ROUGH_FRAC - m._rough_level(m.SURFACE_ROUGH_LEVEL_DEFAULT)) < 1e-12,
                "沙面起伏默认(%s 档) FRAC=%.4f"
                % (m.SURFACE_ROUGH_LEVEL_DEFAULT,
                   m._rough_level(m.SURFACE_ROUGH_LEVEL_DEFAULT)))

            print("\n② 隐藏菜单里有这两行, 且高亮 = 当前档")
            self._open_dev_menu()
            btns = getattr(self, "_dev_level_btns", {})
            chk(set(btns) == {"grain", "rough"}, "两行都在 (_dev_level_btns)")
            gb, rb = btns.get("grain", {}), btns.get("rough", {})
            chk([lb for lb, _g, _c in m.SAND_GRAIN_LEVELS] == list(gb),
                "沙体颗粒 = 6 个按钮 1-6")
            chk([lb for lb, _f in m.SURFACE_ROUGH_LEVELS] == list(rb),
                "沙面起伏 = 6 个按钮 1-6")
            for lb, b in gb.items():
                sel = tuple(round(v, 3) for v in b.background_color[:3]) == \
                      tuple(round(v, 3) for v in m.POPUP_GOLD_SEL[:3])
                chk(sel == (lb == m.current_grain_level()),
                    "  沙体颗粒 %s 的高亮 = %s" % (lb, lb == m.current_grain_level()))
            for lb, b in rb.items():
                sel = tuple(round(v, 3) for v in b.background_color[:3]) == \
                      tuple(round(v, 3) for v in m.POPUP_GOLD_SEL[:3])
                chk(sel == (lb == m.current_rough_level()),
                    "  沙面起伏 %s 的高亮 = %s" % (lb, lb == m.current_rough_level()))

            print("\n③ 点 F / B ⇒ 全局真的变了")
            def mat_hash():
                # ⚠️ **两个旋钮都要传全**: 粗度现在才是唯一维度, 漏掉它就是"永远没换"
                return hashlib.sha256(m._sand_material_rgba(
                    256, base, dark, light, grad=m.SAND_MATERIAL_GRAD,
                    coarse=m.SAND_MATERIAL_COARSE)).hexdigest()[:10]
            before_g = mat_hash()
            before_r = m.UPPER_ROUGH_FRAC
            gb["6"].dispatch("on_press")
            rb["2"].dispatch("on_press")
            chk(m.current_grain_level() == "6", "沙体颗粒 切到 6")
            chk(m.current_rough_level() == "2", "沙面起伏 切到 2")
            after_g = mat_hash()
            chk(after_g != before_g, "材质真的换了 (hash %s -> %s)" % (before_g, after_g))
            chk(abs(m.UPPER_ROUGH_FRAC - 0.0025) < 1e-12,
                "UPPER_ROUGH_FRAC 真的换了 (%.4f -> %.4f)" % (before_r, m.UPPER_ROUGH_FRAC))
            # 沙面数组也要跟着换 —— 绘制与面积求解读的是同一个数组
            import numpy as np
            arr = np.array(hg._upper_rough)
            chk(abs(float(np.abs(arr).max()) - 0.0025 * 2 * hg._R_inner) < 1e-6,
                "widget._upper_rough 数组也换到 B 档了")

            print("\n④ 点同一档两次 = 幂等")
            chk(hg.set_rough_level("2") is False, "set_rough_level 同档返回 False")
            chk(hg.set_grain_level("6") is False, "set_grain_level 同档返回 False")

            print("\n⑤ 落盘存的是标签")
            hg.save_config("金沙")
            import json
            raw = json.loads(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
            chk(raw.get("grain_level") == "6", "grain_level = 6 (实得 %r)" % raw.get("grain_level"))
            chk(raw.get("rough_level") == "2", "rough_level = 2 (实得 %r)" % raw.get("rough_level"))

            print("\n⑥ 重开弹窗, 高亮跟着走")
            self._close_dev_menu()
            self._open_dev_menu()
            gb2 = self._dev_level_btns["grain"]
            sel = [lb for lb, b in gb2.items()
                   if tuple(round(v, 3) for v in b.background_color[:3])
                   == tuple(round(v, 3) for v in m.POPUP_GOLD_SEL[:3])]
            chk(sel == ["6"], "重开后沙体颗粒高亮 = 6 (实得 %s)" % sel)

            bad = [r for r in rows if not r[0]]
            print("\n总计 %d 条, 失败 %d 条" % (len(rows), len(bad)))
            for _ok, lb in bad:
                print("   FAIL:", lb)
            print("总判定:", "通过" if not bad else "**不通过**")
            Clock.schedule_once(lambda dt: self.stop(), 0.2)

    P().run()
    tmp_home.cleanup()
    tmp_cfg.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
