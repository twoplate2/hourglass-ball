#!/bin/bash
# 设备单变量臂: 启停 app + 打标记文件 + 点"开始" + 连拍 N 张 + 量各向异性。
#
#   bash tools/_dev_aniso.sh <标签> [标记名=值 ...]
#   bash tools/_dev_aniso.sh base
#   bash tools/_dev_aniso.sh vy0 flowvy=0
#
# 🔴 本项目铁律: **标记文件是唯一能让设备读到旋钮的通路**(环境变量到不了安卓 app)。
#    所以每臂都把标记目录**列出来并回读**写进日志 —— 标记没被读到的话那一臂是基线空转,
#    会被读成"没差别"(踩过)。
# 🔴 拍完**不停止沙漏** —— 留静止画面等于把用户看结果的眼睛关了。
set -u
ADB="C:/Program Files/Netease/MuMu/nx_main/adb.exe"
SER=127.0.0.1:16416
APP=/data/data/org.shalou.hourglass/files/app
ROOT=/e/AI_Tools/other/shalou_claude/pc/apk
LABEL="$1"; shift
OUT="$ROOT/benchmark_logs/_dev/aniso_$LABEL"
mkdir -p "$OUT"; rm -f "$OUT"/*.png

# ---- 1. 标记文件 ----
# 🔴 **目录有两个, 别写错**(2026-10-10 栽过):
#    `main.py`   的探针读 `dirname(main.py)`    = `<app>/`        (flowrate/trailscale/matmode/refreshmax…)
#    `tools/*.py` 的探针读 `dirname(模块自己)`   = `<app>/tools/`  (holeth/flowvy/sandalpha…)
#    写到错的那一边 = **该臂空转**, 而回读照样能读到, 会被读成"没差别"(已踩过一次)。
#    ⇒ 两边都写(写错的那份被忽略, 无害), 两边都回读。
KNOWN_APP="neckout freeramptubes necklog necknouv matmode flowrate trailscale streamspread neckgrains flowslope refreshmax"
KNOWN_TOOLS="flowvy holeth holeramp holegrow freedrift sandalpha pfade"
"$ADB" -s $SER shell "cd $APP && rm -f $KNOWN_APP; cd $APP/tools 2>/dev/null && rm -f $KNOWN_TOOLS" >/dev/null 2>&1
for kv in "$@"; do
  k="${kv%%=*}"; v="${kv#*=}"
  printf '%s' "$v" | "$ADB" -s $SER shell "cat > $APP/$k; cat > $APP/tools/$k"
done
echo "== 臂 $LABEL =="
echo "-- 标记回读 <app>/ --"
"$ADB" -s $SER shell "cd $APP && for f in $KNOWN_APP; do [ -f \$f ] && echo \"  \$f=\$(cat \$f)\"; done"
echo "-- 标记回读 <app>/tools/ --"
"$ADB" -s $SER shell "cd $APP/tools && for f in $KNOWN_TOOLS; do [ -f \$f ] && echo \"  \$f=\$(cat \$f)\"; done"
echo "-- (两边都空 = 基线)"

# ---- 2. 冷启动 ----
"$ADB" -s $SER shell "am force-stop org.shalou.hourglass"
sleep 1
"$ADB" -s $SER shell "monkey -p org.shalou.hourglass -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1
sleep 7
echo "-- GPU 沙流行 --"
"$ADB" -s $SER logcat -d -t 400 2>/dev/null | grep -iE "sand flow active|batch renderers|Traceback" | tail -3

# ---- 3. 点"开始"(底部启动按钮, 由截图定标) ----
"$ADB" -s $SER shell "input tap 730 1800"
sleep 9        # 让柱子充分建立(50s 档)

# ---- 4. 连拍 8 张 ----
for i in 1 2 3 4 5 6 7 8; do
  "$ADB" -s $SER exec-out screencap -p > "$OUT/f$i.png"
  sleep 0.3
done
echo "-- 拍了 $(ls "$OUT"/*.png 2>/dev/null | wc -l) 张 -> $OUT"
