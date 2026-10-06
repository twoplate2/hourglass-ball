#!/bin/bash
# r31-2号 设备 B 操作助手 (只碰 127.0.0.1:16416)
export MSYS_NO_PATHCONV=1
ADB="/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe"
D="127.0.0.1:16416"
S="E:/AI_Tools/other/shalou_claude/pc/apk/_shot/r31b"

case "$1" in
  tap)   "$ADB" -s $D shell input tap "$2" "$3" ;;
  long)  "$ADB" -s $D shell input swipe "$2" "$3" "$2" "$3" "$4" ;;
  snap)  "$ADB" -s $D shell screencap -p /sdcard/_s.png >/dev/null && "$ADB" -s $D pull /sdcard/_s.png "$S/$2.png" >/dev/null && echo "-> $S/$2.png" ;;
  cfg)   "$ADB" -s $D shell "cat /data/data/org.shalou.hourglass/files/.hourglass_config.json"; echo ;;
  kill)  "$ADB" -s $D shell am force-stop org.shalou.hourglass ;;
  start) "$ADB" -s $D shell monkey -p org.shalou.hourglass -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1; echo started ;;
esac
