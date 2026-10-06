#!/bin/bash
# r31-2号: 打印沙漏 app 当前正在播放的 AudioTrack 概况
export MSYS_NO_PATHCONV=1
ADB="/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe"
D="127.0.0.1:16416"
"$ADB" -s $D shell "dumpsys audio" | grep -E "AudioPlaybackConfiguration" | grep -v "SoundPool" | grep "10053" | \
  sed -E 's/.*piid:([0-9]+).*state:([a-z]+).*sessionId:([0-9]+).*sampleRate:([0-9]+).*/  track piid=\1 state=\2 session=\3 rate=\4/'
