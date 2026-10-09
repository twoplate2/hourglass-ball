# -*- coding: utf-8 -*-
"""设备端末段自检: 「最后 x 秒没有沙子流下, 但下面的沙子体积在增加」。

**为什么要在设备上跑**: 用户 2026-10-10 明确要求「必须在安卓上通过测试才行」。
墙钟截图追一个 0.2~0.4 秒的窗口, 我连试 5 次都没成(采样太粗) ⇒ 换成**确定性的自检**:
只推进 `elapsed` 与物理, 不渲染, 把四栏打出来。判据与桌面探针**逐字相同**。

触发: app 私有目录存在标记文件 `tailcheck`(与 `shrinkmin`/`twoimpl` 同一套)。
  adb shell "echo 50 > /data/data/org.shalou.hourglass/files/app/tailcheck"
  (文件内容 = 周期秒数, 缺省 50)
结果写 `<app>/tailcheck.out` 并 `print` 到 logcat。
"""
import os
import time


def _app_dir():
    """**与 `main.py` 的 `_shrink_min_probe` 同一个目录**(= main.py 所在目录)。

    🔴 2026-10-10 踩过: 原来用 `getFilesDir()` ⇒ 拿到的是 `<pkg>/files`,
    而**所有标记文件(`shrinkmin`/`twoimpl`/`seamband`…)都在 `<pkg>/files/app`**
    ⇒ 自检钩子**永远不触发**, 而且一行报错都没有(静默失效)。
    本模块在 `<app>/tools/` 下 ⇒ **上一级**就是 main.py 所在目录。
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def maybe_run(widget, duration=None):
    """有标记文件就跑自检; 返回 True 表示本次不进入正常 UI 流程。"""
    d = _app_dir()
    flag = os.path.join(d, "tailcheck")
    if not os.path.exists(flag):
        return False
    try:
        with open(flag, "r") as fh:
            period = float(fh.read().strip() or "50")
    except Exception:
        period = 50.0
    if duration:
        period = float(duration)
    lines = ["tailcheck: period=%.1f  (设备自检, 不渲染)" % period]
    try:
        widget.set_duration(period)
        widget.reset()
        widget._rebuild_height_table()
        widget.running = True
        widget.elapsed = 0.0
        dt = 1.0 / 60.0
        t0 = time.perf_counter()
        nxt = max(0.0, period - 12.0)
        rel_full = land_full = None
        lines.append("     t   | upper  | released | flight | landed |  pn")
        while widget.elapsed < period - 1e-9:
            widget.tick(dt)
            if widget.elapsed >= nxt - 1e-9:
                st = widget.sand_transfer_state()
                rel = 1.0 - st["upper"] - st["neck"]
                if rel_full is None and rel >= 0.9995:
                    rel_full = widget.elapsed
                if land_full is None and st["landed"] >= 0.9995:
                    land_full = widget.elapsed
                lines.append("  %6.2f | %6.3f | %8.3f | %6.3f | %6.3f | %3d"
                             % (widget.elapsed, st["upper"], rel, st["flight"],
                                st["landed"], getattr(widget, "pn", -1)))
                nxt += 0.25 if period >= 30 else 0.1
        gap = (None if (rel_full is None or land_full is None)
               else land_full - rel_full)
        lines.append("")
        lines.append("  released->1.0 @ t=%s ; landed->1.0 @ t=%s"
                     % ("%.2f" % rel_full if rel_full else "未达",
                        "%.2f" % land_full if land_full else "未达"))
        lines.append("  ==> 没沙在流但堆还在长的时长 = %s 秒"
                     % ("%.2f" % gap if gap is not None else "不可判"))
        lines.append("  ==> 判据: 上球(upper)在计时结束前不许归零; 该时长应 <= 0.25s")
        lines.append("  (耗时 %.1fs)" % (time.perf_counter() - t0))
    except Exception as exc:
        lines.append("  !! 自检异常: %r" % (exc,))
    try:
        with open(os.path.join(d, "tailcheck.out"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        pass
    for ln in lines:
        print("TAILCHECK %s" % ln)
    return True
