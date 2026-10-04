#!/bin/bash
# 把工作区的 main.py + app_version.py 推到 MuMu 上的 app 私有目录并校验。
# 用法: tools/_review_push.sh [--no-restart]
# ⚠️ 三条硬约束(都踩过):
#   1. app 的 uid 必须**现查**(重装一次就换), 写死会让文件属主不对 ⇒
#      `Entrypoint not found (.py), abort.` ⇒ 应用起不来, 症状像模拟器坏了。
#   2. 必须逐文件比 sha256(不是字节数) —— 陈旧 /data/local/tmp 会把 push 失败掩盖成成功。
#   3. 必须删 main.pyc / app_version.pyc: p4a 是"先 .pyc 后 .py", 残留字节码会让本次改动根本不执行。
set -eu
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
# ⚠️ 钉死设备序列号: 多个评审并行时 adb 会报 "more than one device/emulator",
#    而那时**每条命令都静默失败** —— 表现为"推了但设备没变"。
SER="${SER:-127.0.0.1:16384}"
PKG=org.shalou.hourglass
DEV_APP=/data/data/$PKG/files/app
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
W=$(cd "$ROOT" && pwd -W)

"$ADB" -s "$SER" shell id -u | tr -d '\r' | grep -qx 0 || { echo "!! 需要 adb root"; "$ADB" root; sleep 3; }
APP_UID=$("$ADB" -s "$SER" shell stat -c %u "$DEV_APP" | tr -d '\r')
case "$APP_UID" in ''|*[!0-9]*) echo "!! 取不到 uid, 先手动跑一次 app"; exit 1;; esac
APP_USER="u0_a$((APP_UID - 10000))"

for f in main.py app_version.py frame_benchmark.py; do
  "$ADB" -s "$SER" push "$W/$f" "/data/local/tmp/_rv_$f" >/dev/null
done
"$ADB" -s "$SER" shell "set -e
  cp /data/local/tmp/_rv_main.py         $DEV_APP/main.py
  cp /data/local/tmp/_rv_app_version.py  $DEV_APP/app_version.py
  cp /data/local/tmp/_rv_frame_benchmark.py $DEV_APP/frame_benchmark.py
  chown $APP_USER:$APP_USER $DEV_APP/main.py $DEV_APP/app_version.py $DEV_APP/frame_benchmark.py
  chmod 600 $DEV_APP/main.py $DEV_APP/app_version.py $DEV_APP/frame_benchmark.py
  rm -f $DEV_APP/main.pyc $DEV_APP/app_version.pyc $DEV_APP/frame_benchmark.pyc $DEV_APP/__pycache__/*.pyc"

rc=0
for f in main.py app_version.py frame_benchmark.py; do
  e=$(sha256sum "$ROOT/$f" | cut -d' ' -f1)
  a=$("$ADB" -s "$SER" shell "sha256sum $DEV_APP/$f" | tr -d '\r' | cut -d' ' -f1)
  if [ "$e" = "$a" ]; then echo "  OK   $f  ${e:0:12}"; else echo "  !!   $f 期望 ${e:0:12} 实得 ${a:0:12}"; rc=1; fi
done
[ $rc -eq 0 ] || exit 1
v=$(sed -n 's/.*APP_VERSION *= *"\([^"]*\)".*/\1/p' "$ROOT/app_version.py" | head -1)
echo "设备端已更新到 v$v (uid $APP_UID)"

if [ "${1:-}" != "--no-restart" ]; then
  "$ADB" -s "$SER" shell am force-stop $PKG
  sleep 1
  "$ADB" -s "$SER" shell monkey -p $PKG -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
  sleep 9
  n=$("$ADB" -s "$SER" shell 'ps -A | grep -c org.shalou.hourglass' | tr -d '\r')
  [ "$n" != "0" ] && echo "已重启, 存活 ✓" || { echo "!! 启动后秒退, 查 logcat"; exit 1; }
fi
