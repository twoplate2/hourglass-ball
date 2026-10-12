# -*- coding: utf-8 -*-
"""**GPU 材质的时间型 uniform 有没有真的在动** —— 直接读 `SandFlowContext`, 不看画面。

## 为什么必须有它(2026-10-12, 2.64~2.74 共 11 个版本带病出货)

2.64(`210ab59`)把 `self["sand_mix"] = float(blend)` **顺手挪进了** `if speed_scale !=
self._mat_key:` 那一支 —— 而 `speed_scale`(`_particle_motion_scale`)在一轮里是**常数**
⇒ 那支**只在第一帧跑一次**, 而第一帧 `elapsed ≈ 0` ⇒ `blend ≈ 0` ⇒ **`sand_mix` 被永久
钉在 0**。

后果不是掉色, 是**整层 GPU 颗粒/光照一次都没接上**: 最后一句是
`mix(original.rgb, flowing, sand_mix)` ⇒ 画面上就是**原贴图**(锁定在屏幕上、完全静止)。
用户判词: 「**有部分沙子一动不动, 看起来完全不流动, 大概是从颈部出来的一小段**」+
「**最上层的沙子之前有一个 Gpu 的流动, 也被你给删了**」。

**为什么 11 个版本的闸门全都没抓住它**:
  · 金标准(`_render_golden`)是**静态帧** —— 它比的是"这一版和上一版像不像", 而
    `sand_mix` 逐版本重标定过 ⇒ 每一版都和自己一致;
  · 那些"材质图案跑 220~500px/s"的实测量的是 **coverage 那一套噪声场**
    (`hn`/`n`/`n2`, 走 `adv = sand_clock`, **不受 `sand_mix` 管**) ⇒ **动的不是颗粒**;
  · 渲染脚手架(`inspect_flow`)里 `sand_mix` 的值**取决于 `set_duration` 有没有真的改到
    duration** ⇒ 同一份代码能渲染出两个样子, 拿它做 A/B 会得出"改了没差别"
    (实测: 修好/没修两张图 **md5 相同**)。
⇒ 唯一可靠的判据是**直接读那个 uniform**(本脚本), 外加 `HG_SAND_MIX_PIN` 钉值做 A/B。

## 判据

在一轮真正跑起来之后(两次采样相隔 ≥1s):
  · `sand_mix` 必须 **≥ 0.99**(blend 在 0.3s 后恒为 1.0);
  · `sand_clock` / `sand_time` 必须**变过**(它们每帧都在动);
  · `sand_free` / `sand_tail` / `sand_motion_scale` 允许不变(看周期与状态)。
**负对照**: `HG_SAND_MIX_PIN=0` ⇒ 第一条必须**翻红**(否则脚本没量到东西)。

跑法: python tools/_probe_sandmix.py [--period 50]
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", type=float, default=50.0)
    args = ap.parse_args()

    tmp_home = tempfile.TemporaryDirectory(prefix="sandmix-home-")
    tmp_cfg = tempfile.TemporaryDirectory(prefix="sandmix-cfg-")
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
    # 🔴 红线: 绝不碰真实的 `~/.hourglass_config.json`
    m.config_path = lambda: str(Path(tmp_cfg.name) / "cfg.json")

    WATCH = ("sand_mix", "sand_clock", "sand_time", "sand_free", "sand_motion_scale")

    class P(m.HourglassApp):
        def on_start(self):
            self.samples = []
            Clock.schedule_interval(self.tick, 0.2)

        def tick(self, _dt):
            hg = self.hourglass
            ctxs = getattr(hg, "_sand_flow_contexts", ()) or ()
            if not ctxs:
                # 画布要等一帧才建好 —— 前 1.5s 静默等, 再没有才算"材质没起来"
                self.waited = getattr(self, "waited", 0.0) + 0.2
                if self.waited > 1.5:
                    print("!! 没有 shader context(材质没起来) —— 这条查不了")
                    self.stop()
                return
            if not hg.running and hg.elapsed <= 0.0:
                hg.set_duration(args.period)
                hg.toggle()
            self.samples.append(
                (hg.elapsed, {k: ctxs[-1][k] for k in WATCH}))
            # 第一帧 elapsed≈0 也采一次(那正是缺陷发生的地方), 之后等它跑起来再采一次
            if len(self.samples) >= 2 and self.samples[-1][0] >= 1.2:
                self.report()

        def report(self):
            a, b = self.samples[0], self.samples[-1]
            print("采样: t=%.3fs vs t=%.3fs (颈部 context)" % (a[0], b[0]))
            bad = []
            for k in WATCH:
                va, vb = a[1][k], b[1][k]
                print("   %-18s %-28s → %s" % (k, va, vb))
            mix = float(b[1]["sand_mix"])
            ok_mix = mix >= 0.99
            print("   判据① sand_mix ≥ 0.99 : %s (实得 %.3f)" % ("PASS" if ok_mix else "FAIL", mix))
            bad.append(ok_mix)
            for k in ("sand_clock", "sand_time"):
                ch = a[1][k] != b[1][k]
                print("   判据② %s 变过 : %s" % (k, "PASS" if ch else "FAIL"))
                bad.append(ch)
            print("总判定:", "通过" if all(bad) else "**不通过 —— GPU 颗粒层没接上(见文件头)**")
            self.stop()

    P().run()
    tmp_home.cleanup()
    tmp_cfg.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
