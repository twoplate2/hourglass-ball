#!/bin/bash
# 长按区的**设备实测**(2026-10-07): 新长出来那一条真的能开开发者菜单吗? 会不会抢到画布?
#
# ## 为什么必须回设备
#
# 桌面探针(`_probe_hold_area.py`)只能验"Kivy 把这一下派发给了谁" —— 而这里的判据是
# **屏幕上真的出现了什么**。项目的既有教训: 桌面看不见 ≠ 设备看不见。
#
# ## 三个点, 两正一负
#
#   A 新长出来那一条(底栏上方)  → 期望**开发者菜单弹出**(这是这次改动的目的)
#   B 老位置(底栏里)            → 期望**弹出**(正对照: 改动没把老入口弄坏)
#   C 再往上一截(画布里)        → 期望**不弹**(负对照: 扩大区没有无限蔓延)
#
# 判据用**截图的像素差**: 开发者菜单是一整块暖白弹窗 ⇒ 全屏差异很大;
# 而"点沙漏开始/暂停"只会让沙子动 ⇒ 差异很小。阈值 15%。
#
# 用法: bash tools/_probe_hold_device.sh
set -u
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
SER="${SER:-127.0.0.1:16384}"
PKG=org.shalou.hourglass
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/benchmark_logs/holdprobe"
mkdir -p "$OUT"
W=$(cd "$OUT" && pwd -W)

say() { echo "[hold] $*"; }

shot() {          # $1 = 名字
  "$ADB" -s "$SER" shell screencap -p /sdcard/_hp.png >/dev/null 2>&1
  "$ADB" -s "$SER" pull /sdcard/_hp.png "$W/$1.png" >/dev/null 2>&1
  [ -s "$OUT/$1.png" ] || say "!! 截图 $1 失败"
}

bash "$ROOT/tools/_review_push.sh" >/tmp/_hp_push.log 2>&1 || { say "!! 推送失败"; tail -5 /tmp/_hp_push.log; exit 1; }
grep -q "存活" /tmp/_hp_push.log || { say "!! 应用没起来"; exit 1; }
say "推送并重启 OK"

# ⚠️ **每个点都从"重启后的主界面"开始** —— 第一版在三格之间用 BACK 关弹窗,
#    结果 BACK 把 app 直接退掉了, 后两格全在桌面上量的("没弹"是假象)。
#    教训: 探针的两格之间**必须回到确定状态**, 不能靠"退一步应该就回去了"。
cycle() {         # $1=名字 $2=y
  "$ADB" -s "$SER" shell am force-stop $PKG >/dev/null
  sleep 1
  "$ADB" -s "$SER" shell monkey -p $PKG -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
  sleep 7
  shot "$1_before"
  "$ADB" -s "$SER" shell input swipe 546 "$2" 546 "$2" 3500 >/dev/null
  sleep 1.5
  shot "$1_after"
  say "$1 (y=$2) 拍完"
}

cycle new_area 2200
cycle old_area 2328
cycle far_above 1800

echo "----------------------------------------------------------------"
python - "$OUT" <<'PY'
import sys
from pathlib import Path
from PIL import Image, ImageChops
d = Path(sys.argv[1])
def load(p):
    return Image.open(p).convert("L").resize((190, 290))
def diff(name):
    fa, fb = d / (name + "_before.png"), d / (name + "_after.png")
    if not (fa.exists() and fb.exists()):
        return None
    px = list(ImageChops.difference(load(fa), load(fb)).getdata())
    return sum(1 for v in px if v > 30) / len(px)
for name, want in (("new_area", "弹"), ("old_area", "弹"), ("far_above", "不弹")):
    f = diff(name)
    if f is None:
        print("  %-10s 没截到图" % name); continue
    got = "弹" if f > 0.15 else "不弹"
    print("  %-10s 差异 %5.1f%%  => 菜单 %s   (期望 %s)%s"
          % (name, f * 100, got, want, "" if got == want else "   <<< 不符"))
PY
