#!/bin/bash
# 一轮**设备端函数级剖面**: 推工作区 -> 开 `prof.on` -> 重启 -> 驱动基准 -> 收 HGPROF。
#
# 与 `_one_bench.sh` 的区别: 那个量四栏(物理/图元/Canvas/前次Swap), 这个量**函数**。
# ⚠️ 两者**不要同时开**(理由见 `tools/prof_android.py` 顶部口径声明)。
#
# 用法: tools/_one_prof.sh [标签] [驱动秒数=170]
set -eu
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
SER="${SER:-127.0.0.1:16384}"
PKG=org.shalou.hourglass
DEV_APP=/data/data/$PKG/files/app
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${1:-prof}"
WAIT="${2:-170}"
LOG="$ROOT/benchmark_logs/prof_${TAG}.log"
mkdir -p "$ROOT/benchmark_logs"

# ① 推工作区(**不重启** —— 标记文件必须在 app 启动前就在)
bash "$ROOT/tools/_review_push.sh" --no-restart
# ② 开标记(属主必须对, 否则 app 读不到) + 重启
UID_=$("$ADB" -s "$SER" shell stat -c %u "$DEV_APP" | tr -d '\r')
U="u0_a$((UID_ - 10000))"
"$ADB" -s "$SER" shell "touch $DEV_APP/prof.on && chown $U:$U $DEV_APP/prof.on && ls -l $DEV_APP/prof.on" | tr -d '\r'
"$ADB" -s "$SER" shell am force-stop $PKG
sleep 1
"$ADB" -s "$SER" shell monkey -p $PKG -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
sleep 10
n=$("$ADB" -s "$SER" shell 'ps -A | grep -c org.shalou.hourglass' | tr -d '\r')
[ "$n" != "0" ] || { echo "!! 启动后秒退"; exit 1; }

# ③ 收日志(后台流式, 免得 ring buffer 被冲掉)
"$ADB" -s "$SER" logcat -c
"$ADB" -s "$SER" logcat > "$LOG" 2>&1 &
LC=$!
sleep 2
# ④ 驱动基准(坐标 = 1080x2400 设备真实像素, 与 `_one_bench.sh` 同一套)
"$ADB" -s "$SER" shell input swipe 546 2328 547 2328 3500
sleep 3
"$ADB" -s "$SER" shell input tap 540 2076
sleep 2
"$ADB" -s "$SER" shell input tap 255 1524
echo "[$TAG] 基准已驱动, 等 ${WAIT}s 收尾"
sleep "$WAIT"
kill $LC 2>/dev/null || true
sleep 1
"$ADB" -s "$SER" shell rm -f $DEV_APP/prof.on || true
echo "=== HGPROF 行 ==="
grep -a "HGPROF" "$LOG" | sed 's/^.*HGPROF/HGPROF/' | tail -40
echo "=== 落盘 $LOG ==="
