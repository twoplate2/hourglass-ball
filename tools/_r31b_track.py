# -*- coding: utf-8 -*-
"""r31-2号: 打印沙漏 app 的 AudioTrack 指纹(FrmCnt 可区分 wav 时长: 720000=15s 沙, 389376=8.112s 钟表, 672000=14s 水/风)。"""
import re
import subprocess
import sys

ADB = r"C:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe"
D = "127.0.0.1:16416"
out = subprocess.run([ADB, "-s", D, "shell", "dumpsys media.audio_flinger"],
                     capture_output=True, text=True, errors="replace").stdout
rows = []
for line in out.splitlines():
    if "10053" not in line or not line.strip().startswith("S"):
        continue
    m = re.search(r"\sA\s+0x[0-9a-f]{3}\s+\S+\s+\S+\s+(\d+)\s+(\d+)\s+(\d+)\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S)", line)
    pid = re.search(r"/\s*(\d+)\s+(\d+)\s+", line)
    sess = re.search(r"\s(\d{3,5})\s+(\d+)\s+A\s+0x", line)
    if sess:
        sr = re.search(r"\s(24000|44100|48000)\s+\d+\s+\d+\s+\d+", line)
        frm = re.search(r"\s([0-9A-F]{8})\s+(\d+)\s+(\d+)\s+([A-Za-z])\s", line)
        rows.append((sess.group(1), sess.group(2), sr.group(1) if sr else "?", frm.groups() if frm else None))
for r in rows:
    print("  session=%s port=%s rate=%s  FrmCnt/FrmRdy/flags = %s" % (r[0], r[1], r[2], r[3]))
if not rows:
    print("  (没有 active 的 AudioTrack)")
