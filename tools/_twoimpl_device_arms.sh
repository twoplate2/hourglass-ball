#!/bin/bash
# `twoimpl` 档位的**设备口径**对照: 取颈部+自由柱截图, 存 benchmark_logs/_vid/twoimpl_dev/。
set -eu
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
SER="${SER:-127.0.0.1:7555}"
PKG=org.shalou.hourglass
DEV_APP=/data/data/$PKG/files/app
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/benchmark_logs/_vid/twoimpl_dev"
mkdir -p "$OUT"
WAIT_START="${WAIT_START:-5}"
sh_() { "$ADB" -s "$SER" shell "$@"; }
for M in 0 3; do
  sh_ "echo $M > $DEV_APP/twoimpl"
  sh_ "chown \$(stat -c %u $DEV_APP):\$(stat -c %g $DEV_APP) $DEV_APP/twoimpl"
  sh_ "am force-stop $PKG" || true
  sleep 2
  sh_ "monkey -p $PKG -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1 || true
  sleep 7
  sh_ "input tap 540 1300" >/dev/null 2>&1 || true
  sleep "$WAIT_START"
  "$ADB" -s "$SER" exec-out screencap -p > "$OUT/m$M.png"
  echo "  [twoimpl=$M] -> $OUT/m$M.png"
done
sh_ "rm -f $DEV_APP/twoimpl"
