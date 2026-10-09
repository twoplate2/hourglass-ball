#!/bin/bash
# "沙柱收到多细" 的**设备口径**四臂对照(标记文件 `shrinkmin`)。
# ⚠️ 环境变量到不了安卓 app ⇒ 只能走标记文件。⚠️ 只碰 127.0.0.1:7555。
set -eu
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
SER="${SER:-127.0.0.1:7555}"
PKG=org.shalou.hourglass; DEV_APP=/data/data/$PKG/files/app
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; W=$(cd "$ROOT" && pwd -W)
OUT="$ROOT/benchmark_logs/_vid/shrink_dev"; mkdir -p "$OUT"
sh_() { "$ADB" -s "$SER" shell "$@"; }
for ARM in ${ARMS:-0.70 0.50 0.35 0.20}; do
  TAG="s$(echo "$ARM" | tr -d '.')"
  sh_ "echo $ARM > $DEV_APP/shrinkmin"
  sh_ "chown \$(stat -c %u $DEV_APP):\$(stat -c %g $DEV_APP) $DEV_APP/shrinkmin"
  sh_ "am force-stop $PKG" || true; sleep 2
  sh_ "monkey -p $PKG -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1 || true
  sleep 6; sh_ "input tap 540 1300" >/dev/null 2>&1 || true
  sleep "${WAIT_START:-14}"
  "$ADB" -s "$SER" exec-out screencap -p > "$OUT/$TAG.png"
  echo "  shrinkmin=$ARM -> $TAG.png"
done
sh_ "rm -f $DEV_APP/shrinkmin"; echo "  截图 -> benchmark_logs/_vid/shrink_dev/"
