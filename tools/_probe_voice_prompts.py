# -*- coding: utf-8 -*-
"""操作提示音烟测: 三个操作**该播的播、不该播的一条都不播**(2026-10-07)。

用户逐条拍定的文案:
    换沙色   → 「金沙」(c0..c5, 与 SAND_PRESETS 同序)
    改周期   → 「计时时间设定为一分钟」(pre_time + 时长词序) —— **只在点「确定」且真的变了**
    换提示音 → 「提示音设定为沙沙声」(pre_sound + e0..e4) —— **真的切换成功才播**

## 怎么判

把 `main._SoundProxy` 换成记录器(不碰真音频), 然后**直接调三个入口函数**。
播了哪句从**缓存文件名**反推 —— `_voice_say` 把 wav 存成 `"_".join(keys).wav`,
所以文件名就是词块键 ✓。

## 三条必须验到的

1. **该播的播**: 6 个色块 → c0..c5 各一条; 周期确定(真的变了) → 一条 pre_time+…;
   音效切换成功 → 一条 pre_sound+e?。
2. **不该播的一条不播**: 周期弹窗里点基础时间 / 点倍数 / 拖滑杆 → **0 条**
   (用户明确要求); 点确定但周期没变 → 0 条; 音效同名连点 → 只第一条播。
3. **负对照**: 把 `_voice_bank` 置 None 再走上面三条 → **不炸、不播**
   (这才是"词库还没加载完"时用户真会碰到的路)。

跑法: python tools/_probe_voice_prompts.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("KIVY_METRICS_DENSITY", "2.75")
os.environ["HG_FLOW_RENDERER"] = "texture"

from kivy.clock import Clock                     # noqa: E402
from kivy.core.window import Window              # noqa: E402
import main as m                                 # noqa: E402

# ⚠️ 桩**必须返回一个像样的 proxy 对象** —— 返回 None 会让 `_set_sound` 走
#    "新建失败 ⇒ 旧态保留 ⇒ 返回 False" 那条**设计好的**分支, 于是所有音效切换
#    都"没生效", 看上去像 bug。踩过一次。
m.HourglassWidget._make_sound_proxy = lambda *a, **kw: _FakeProxy("stub.wav", loop=True)
m.HourglassWidget._make_completion_sound = lambda *a, **kw: None
m.HourglassApp.on_completed = lambda *_: None
m.HourglassWidget.load_config = lambda *_: {"duration": 15.0}
m.HourglassWidget.save_config = lambda *_: None

PLAYED = []          # 播放过的词块键序列


class _FakeProxy:
    def __init__(self, path, loop=True):
        self.backend = "fake"
        # ⚠️ **只记语音提示** —— 背景音/音效代理也走同一个 `_SoundProxy`, 不筛就会把
        #    "stub" 也记进来, 看上去像"多播了一句"。提示音的 wav 恒在 `voice_cache/` 下。
        if "voice_cache" in str(path):
            PLAYED.append(os.path.basename(str(path))[:-4])
        self.path = path

    def play(self): pass
    def stop(self): pass
    def close(self): pass


class _FakePopup:
    def dismiss(self): pass


class _FakeBtn:
    background_color = None
    color = None


class Probe(m.HourglassApp):
    def on_start(self):
        Window.size = (400, 800)
        Clock.schedule_once(self.go, 1.5)

    def go(self, _dt):
        m._SoundProxy = _FakeProxy
        Clock.schedule_once(self.check, 0.6)          # 等词库真的加载完(它延迟 0.05s)

    def check(self, _dt):        # ⚠️ 不能叫 `run` —— 撞 Kivy `App.run()`
        hg, app = self.hourglass, self
        ok = []

        def check(name, want, got):
            good = (want == got)
            ok.append(good)
            print("   %s %-46s 期望 %-22s 实得 %s"
                  % ("✓" if good else "✗", name, want, got))

        print("\n== 词库 ==")
        print("   ok=%s rate=%s clips=%d"
              % (hg._voice_bank.ok, hg._voice_bank.rate, len(hg._voice_bank.clips)))
        if not hg._voice_bank.ok:
            print("   词库不可用 ⇒ 后面都不作数"); self.stop(); return

        print("\n== ① 换沙色: 6 个色块各一次 ==")
        for i, (name, base, dark, light) in enumerate(m.SAND_PRESETS):
            PLAYED.clear()
            app.on_color(base, dark, light, name)
            check("点「%s」" % name, ["c%d" % i], PLAYED[:])

        print("\n== ② 改周期: 弹窗里调参**不播**, 只有确定且真变了才播 ==")
        # 结构取证: "点基础时间/倍数/拖滑杆不播"是靠**没在那三个函数里接线**实现的,
        # 所以直接读源码确认里面没有 `_voice_say`(硬调它们要吃弹窗内部控件, 不值当)。
        import inspect
        for fname in ("_on_base_picked", "_on_mult_picked", "_on_slider_moved"):
            src = inspect.getsource(getattr(app, fname))
            check("源码 %s 里没有 _voice_say" % fname, True, "_voice_say" not in src)
        hg.set_duration(15.0)
        PLAYED.clear()
        app._pick_duration(hg.duration, _FakePopup())        # 确定, 但周期没变
        check("点确定但周期没变", [], PLAYED[:])
        PLAYED.clear()
        app._pick_duration(90.0, _FakePopup())               # 确定, 真变了
        check("点确定且周期真的变了(90s)", ["pre_time_n1_min_n30_sec"], PLAYED[:])
        print("       词序 = %s" % (hg._voice_bank.sentence_keys(90.0),))

        print("\n== ③ 换提示音: 同名连点只播一次, 换选项再播, 选无声音也念 ==")
        hg._set_sound("沙沙声")
        PLAYED.clear()
        btns = {n: _FakeBtn() for n, _p in m.SOUND_OPTIONS}
        app._on_sound_picked("沙沙声", btns)     # 同名 ⇒ _set_sound 返回 False
        check("连点当前已选的音效", [], PLAYED[:])
        PLAYED.clear()
        app._on_sound_picked("水流声", btns)
        check("切到「水流声」", ["pre_sound_e1"], PLAYED[:])
        PLAYED.clear()
        app._on_sound_picked("无声音", btns)
        check("切到「无声音」(最需要确认的一种)", ["pre_silent"], PLAYED[:])

        print("\n== ④ 负对照: 词库为 None(第一帧之前) ==")
        bank, hg._voice_bank = hg._voice_bank, None
        crashed = None
        try:
            PLAYED.clear()
            app.on_color(*m.SAND_PRESETS[2][1:], m.SAND_PRESETS[2][0])
            app._pick_duration(300.0, _FakePopup())
            app._on_sound_picked("风声", btns)
        except Exception as exc:
            crashed = repr(exc)
        check("词库 None 时走三条路径", "不炸且不播", "炸了: %s" % crashed if crashed else
              ("不炸且不播" if not PLAYED else "不炸但播了 %s" % PLAYED))
        hg._voice_bank = bank

        print("\n==> %s (%d/%d)" % ("全部通过" if all(ok) else "**有失败**", sum(ok), len(ok)))
        self.stop()


Probe().run()
