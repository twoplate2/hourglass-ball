#!/bin/bash
# 把工作区的 main.py + app_version.py (+ 运行时耦合的 tools 模块) 推到 MuMu 上的 app 私有目录并校验。
# 用法: tools/_review_push.sh [--no-restart]
# ⚠️ 四条硬约束(前三条踩过, 第四条 2026-10-06 查出来):
#   1. app 的 uid 必须**现查**(重装一次就换), 写死会让文件属主不对 ⇒
#      `Entrypoint not found (.py), abort.` ⇒ 应用起不来, 症状像模拟器坏了。
#   2. 必须逐文件比 sha256(不是字节数) —— 陈旧 /data/local/tmp 会把 push 失败掩盖成成功。
#   3. 必须删 main.pyc / app_version.pyc: p4a 是"先 .pyc 后 .py", 残留字节码会让本次改动根本不执行。
#   4. 🔴 **`tools/` 下那几个被 main.py 运行时 import 的模块也要推**(2026-10-06 发现)。
#      设备上 `tools/` 里只有 **.pyc**(源码不进包, 实测 0 个 .py / 29 个 .pyc), 而
#      `main.py:60` 是 `import flow_numpy`(从 `<app>/tools` 的 sys.path) ——
#      ⇒ **只推 main.py 会把两者推成错配的一对**。
#      实证: 两台设备的 `flow_numpy.pyc` 分别是 10-05 19:00 / 10-06 00:57, 而改它的
#      **1.128 提交于 10-06 04:34** ⇒ 设备跑的是"新 main.py(把 `shrink_min` 传进 consts)
#      + 旧 flow_numpy(硬编码 0.70、**根本不读那个键**)"。
#      后果: 1.128 声称的"两条路径不可能再各写各的"**在设备上是假的** —— 一旦有人设
#      `HG_FLOW_SHRINK_MIN`, 标量路径听、numpy 路径不听。
#      (`tools/ab_device_benchmark.sh` 早就在推这套文件了, 是**日常直推**这条漏了。)
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

# main.py 运行时**按名字 import** 的 tools 模块(见 main.py:60 与 :5459-5469)。
# 改了其中任何一个, 或改了 main.py 里与它们交换的键, 都必须一起推 —— 否则设备上是错配的一对。
TOOLS_MODULES="flow_numpy.py flow_texture_experiment.py flow_batch_experiment.py flow_gpu_experiment.py flow_splash_experiment.py"
# ⚠️ **新增一个被 main.py 运行时 import 的 tools 模块, 必须加进这一行** ——
#   2026-10-07 踩过: 漏加 `flow_splash_experiment.py` ⇒ 设备上 `import` 失败 ⇒
#   main.py 的 except 捕获后**静默回退**原路径 ⇒ 设备 A/B 量出来"两条路径一模一样"
#   (实际是根本没装上)。报错在 logcat 里, 但**默认不会去看**。
TOP_FILES="main.py app_version.py frame_benchmark.py"

"$ADB" -s "$SER" shell id -u | tr -d '\r' | grep -qx 0 || { echo "!! 需要 adb root"; "$ADB" -s "$SER" root; sleep 3; }
APP_UID=$("$ADB" -s "$SER" shell stat -c %u "$DEV_APP" | tr -d '\r')
case "$APP_UID" in ''|*[!0-9]*) echo "!! 取不到 uid, 先手动跑一次 app"; exit 1;; esac
APP_USER="u0_a$((APP_UID - 10000))"

for f in $TOP_FILES; do
  "$ADB" -s "$SER" push "$W/$f" "/data/local/tmp/_rv_$f" >/dev/null
done
for f in $TOOLS_MODULES; do
  "$ADB" -s "$SER" push "$W/tools/$f" "/data/local/tmp/_rvt_$f" >/dev/null
done
"$ADB" -s "$SER" shell "set -e
  cp /data/local/tmp/_rv_main.py         $DEV_APP/main.py
  cp /data/local/tmp/_rv_app_version.py  $DEV_APP/app_version.py
  cp /data/local/tmp/_rv_frame_benchmark.py $DEV_APP/frame_benchmark.py
  mkdir -p $DEV_APP/tools
  for f in $TOOLS_MODULES; do cp /data/local/tmp/_rvt_\$f $DEV_APP/tools/\$f; done
  chown $APP_USER:$APP_USER $DEV_APP/main.py $DEV_APP/app_version.py $DEV_APP/frame_benchmark.py
  chmod 600 $DEV_APP/main.py $DEV_APP/app_version.py $DEV_APP/frame_benchmark.py
  for f in $TOOLS_MODULES; do chown $APP_USER:$APP_USER $DEV_APP/tools/\$f; chmod 600 $DEV_APP/tools/\$f; done
  rm -f $DEV_APP/main.pyc $DEV_APP/app_version.pyc $DEV_APP/frame_benchmark.pyc $DEV_APP/__pycache__/*.pyc
  rm -f $DEV_APP/tools/*.pyc
  rm -rf $DEV_APP/tools/__pycache__"

rc=0
for f in $TOP_FILES; do
  e=$(sha256sum "$ROOT/$f" | cut -d' ' -f1)
  a=$("$ADB" -s "$SER" shell "sha256sum $DEV_APP/$f" | tr -d '\r' | cut -d' ' -f1)
  if [ "$e" = "$a" ]; then echo "  OK   $f  ${e:0:12}"; else echo "  !!   $f 期望 ${e:0:12} 实得 ${a:0:12}"; rc=1; fi
done
for f in $TOOLS_MODULES; do
  e=$(sha256sum "$ROOT/tools/$f" | cut -d' ' -f1)
  a=$("$ADB" -s "$SER" shell "sha256sum $DEV_APP/tools/$f" | tr -d '\r' | cut -d' ' -f1)
  if [ "$e" = "$a" ]; then echo "  OK   tools/$f  ${e:0:12}"; else echo "  !!   tools/$f 期望 ${e:0:12} 实得 ${a:0:12}"; rc=1; fi
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
