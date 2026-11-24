# -*- coding: utf-8 -*-
"""按 tailcheck 指定的周期推进物理，末段沙量和流量的自检结果写入 tailcheck.out。"""
import os
import time


def _app_dir():
    """标记文件位于 main.py 所在目录。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
