#!/bin/bash
# 2.60 设备三臂: 材质「速度/颗粒」实验 (2026-10-11)
#   A=基线(drift 0, coarse 1.0)  B=drift 1.0  C=drift 1.0 + coarse 0.35
# 每臂: 清标记 -> 写标记(两边目录都写) -> 回读 -> 冷启动 -> 点开始 -> 等8s -> 录4s -> 拉回
set -u
ADB="/c/Program Files/Netease/MuMu/nx_main/adb.exe"
SER=127.0.0.1:16416
APP=/data/data/org.shalou.hourglass/files/app
OUT=/e/AI_Tools/other/shalou_claude/pc/apk/benchmark_logs/_dl/260
KNOWN="freedrift freecoarse freegrain flowvy holeth trailscale flowrate flowvy matmode neckgrains streamspread refreshmax pfade sandalpha holeramp holegrow"
arm="$1"; shift
"$ADB" -s $SER shell "cd $APP && rm -f $KNOWN; cd $APP/tools && rm -f $KNOWN" >/dev/null 2>&1
for kv in "$@"; do
  k="${kv%%=*}"; v="${kv#*=}"
  printf '%s' "$v" | "$ADB" -s $SER shell "cat > $APP/$k"
  printf '%s' "$v" | "$ADB" -s $SER shell "cat > $APP/tools/$k"
done
echo "== 臂 $arm =="
"$ADB" -s $SER shell "cd $APP/tools && for f in $KNOWN; do [ -f \$f ] && echo \"  tools/\$f=\$(cat \$f)\"; done"
"$ADB" -s $SER shell "am force-stop org.shalou.hourglass"
sleep 1
"$ADB" -s $SER shell "monkey -p org.shalou.hourglass -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1
sleep 7
"$ADB" -s $SER shell "input tap 904 1800"   # 重置(确保从头)
sleep 0.6
"$ADB" -s $SER shell "input tap 730 1800"   # 开始
sleep 8
"$ADB" -s $SER shell "rm -f /sdcard/arm.mp4"
"$ADB" -s $SER shell "screenrecord --time-limit 4 --bit-rate 24000000 --size 1080x1920 /sdcard/arm.mp4"
MSYS_NO_PATHCONV=1 "$ADB" -s $SER pull /sdcard/arm.mp4 "E:/AI_Tools/other/shalou_claude/pc/apk/benchmark_logs/_dl/260/arm_$arm.mp4" | tail -1
