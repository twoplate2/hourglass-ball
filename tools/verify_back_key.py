# -*- coding: utf-8 -*-
"""验证 Android 返回键不再退出 App —— `_SandBgPopup._handle_keyboard`。

## 背景（两处 Kivy 源码）
    ModalView._handle_keyboard:
        if key == 27 and self.auto_dismiss: self.dismiss(); return True   # 只在 True 时**消费**
    Window.on_keyboard (收尾):
        if not self.dispatch('on_request_close', source='keyboard'):      # 没人消费 ⇒ 关窗
本 App 的弹窗**全是 auto_dismiss=False** ⇒ 27 不被消费 ⇒ 冒到 Window ⇒ 关窗/退出。
(r14-2号 报; r15-1号 在完成/周期/音效三个弹窗上逐个复现, 2026-10-05)

## 判据（四个弹窗各一条）
  ① **关窗探针 = 0** —— "App 会不会退出"的机制本体
  ② **kb 探针 = 0** —— 事件确实被消费了
  ③ 弹窗关掉了  ④ App 侧引用清干净

## ⚠️ 两个坑（都是这个测试自己踩出来的）
1. **别在弹窗还活着的时候改它的类方法。** 第一版在同一个进程里来回 monkeypatch ——
   但 Kivy 的回调是按**方法名**存的（`weakmethod.py` 存 `method_name`，调用时
   `getattr(proxy, method_name)`），类属性一换名字就对不上 ⇒ `unbind` 静默失败 ⇒
   上一个弹窗的处理函数留在 Window 上把后面的事件全吃掉 ⇒ 第 2 个弹窗起全部假失败。
   ⇒ 所以修复版/对照版**各跑一个独立进程**。
2. **`on_dismiss` 回调必须返回 `None`。** Kivy `ModalView.dismiss()` 紧接着就有
   `if self.dispatch('on_dismiss') is True: return` —— **回调返回 True 会把整个关闭动作取消**。
   元组表达式的 lambda 是踩这个坑的典型写法（本次真的踩了：音效弹窗按返回/按确定都关不掉）。

跑法:
    python tools/verify_back_key.py fixed      # 修复后
    python tools/verify_back_key.py control    # 负对照: 换回 Kivy 原版, 必须**失败**
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_BACK = 27
CASES = ["周期", "音效", "完成", "调试"]


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "fixed").lower()
    control = (mode == "control")
    with tempfile.TemporaryDirectory(prefix="backkey-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        sys.path.insert(0, str(ROOT))
        import main as m
        from kivy.clock import Clock
        from kivy.core.window import Window

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 30}
        m.HourglassWidget.save_config = lambda *_: None

        if control:      # 只在本进程开始、**任何弹窗创建之前**改一次
            m._SandBgPopup._handle_keyboard = m._SandBgPopup.__mro__[1]._handle_keyboard

        hits = {"kb": 0, "close": 0}
        Window.bind(on_keyboard=lambda *a: hits.__setitem__("kb", hits["kb"] + 1))
        Window.bind(on_request_close=lambda *a, **k: (hits.__setitem__(
            "close", hits["close"] + 1), True)[1])       # 挡住真关窗, 否则会杀掉测试进程

        rows = []
        st = {"i": 0, "ref": None, "popup": None}

        def close_leftovers(app):
            for attr in ("_sound_popup", "_completion_popup", "_dev_popup"):
                p = getattr(app, attr, None)
                if p is not None:
                    try:
                        p.dismiss()
                    except Exception:
                        pass
                    setattr(app, attr, None)
            for w in list(Window.children):
                if isinstance(w, m._SandBgPopup):
                    try:
                        w.dismiss()
                    except Exception:
                        pass

        def open_case(app, name):
            if name == "周期":
                app.on_duration_picker()
                return None
            if name == "音效":
                app.on_sound_picker()
                return "_sound_popup"
            if name == "完成":
                app.on_completed(30)
                return "_completion_popup"
            app._open_dev_menu()
            return "_dev_popup"

        def step(app, _dt):
            if st["i"] >= len(CASES):
                return finish(app)
            name = CASES[st["i"]]
            st["case"] = name
            close_leftovers(app)
            st["ref"] = open_case(app, name)
            Clock.schedule_once(lambda dt: fire(app), 0.4)

        def fire(app):
            p = getattr(app, st["ref"], None) if st["ref"] else None
            if p is None:
                p = next((w for w in Window.children
                          if isinstance(w, m._SandBgPopup)), None)
            st["popup"] = p
            hits["kb"] = hits["close"] = 0
            Window.dispatch("on_keyboard", KEY_BACK, 0, "", [])
            Clock.schedule_once(lambda dt: record(app), 0.6)

        def record(app):
            p = st["popup"]
            rows.append({
                "name": st["case"], "kb": hits["kb"], "close": hits["close"],
                "closed": bool(p is not None
                               and getattr(p, "_is_open", True) is False),
                "ref_clean": (getattr(app, st["ref"], None) is None
                              if st["ref"] else True),
            })
            st["i"] += 1
            Clock.schedule_once(lambda dt: step(app, dt), 0.25)

        def finish(app):
            print("\n=== 模式: %s ===" % ("负对照(Kivy原版)" if control else "修复后"))
            print("%-6s %-9s %-9s %-8s %-8s %s"
                  % ("弹窗", "kb探针", "关窗探针", "已关闭", "引用清", "判定"))
            all_ok = True
            for r in rows:
                if control:
                    ok = r["close"] > 0                      # 对照: **必须**试图关窗
                    verdict = "OK(如预期失败)" if ok else "**对照没触发 — 无分辨力**"
                else:
                    ok = (r["close"] == 0 and r["kb"] == 0
                          and r["closed"] and r["ref_clean"])
                    verdict = "OK" if ok else "**失败**"
                all_ok &= ok
                print("%-6s %-9d %-9d %-8s %-8s %s"
                      % (r["name"], r["kb"], r["close"],
                         "是" if r["closed"] else "否",
                         "是" if r["ref_clean"] else "否", verdict))
            print("%s: %s" % ("本模式判定", "通过" if all_ok else "**不通过**"))
            app.stop()

        class P(m.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.go, 1.0)

            def go(self, _dt):
                Clock.schedule_once(lambda dt: step(self, dt), 0.5)

        P().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
