#!/bin/bash
# r31-2号: 打印沙漏 app 当前 AudioTrack 的指纹 (FrmCnt 能区分 wav 时长)
export MSYS_NO_PATHCONV=1
ADB="/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe"
D="127.0.0.1:16416"
"$ADB" -s $D shell "dumpsys media.audio_flinger" 2>/dev/null | grep "^ *S " | grep "10053" | \
  awk '{print "  track active="$4" session="$8" FrmCnt="$24" FrmRdy="$25" flags="$7" sr="$18}'
