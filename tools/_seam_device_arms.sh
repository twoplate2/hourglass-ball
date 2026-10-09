#!/bin/bash
# 交界过渡: **设备口径**的四臂对照 + 量化判据 J / R。
#
#   J = 交界 ±(0.15×直径) 窗口内, 相邻 3px 窗口的 |Δmu| **最大值**(行均值) → 越小越平滑
#   R = 同窗口内 mu(y) 的**局部极值个数**(幅度 <1 灰阶的不计) → 过渡**不许制造新条带**
#   通过: J_new < J_old  且  R_new <= R_old
#
# ⚠️ `J` 对"加宽带宽"单调改善 ⇒ **单看 J 就是缺陷与指标同一个东西**(1.23/1.24 vena 事故
#    的同一个坑)。`R` 是防这个的那一半, 不是陪衬。
# ⚠️ 环境变量到不了安卓 app ⇒ 带宽走**标记文件**(`seamband` / `seamanchor`)。
# ⚠️ 只碰 127.0.0.1:7555, 另一个实例不进。
set -eu
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
SER="${SER:-127.0.0.1:7555}"
PKG=org.shalou.hourglass
DEV_APP=/data/data/$PKG/files/app
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
W=$(cd "$ROOT" && pwd -W)
OUT="$ROOT/benchmark_logs/_vid/seam_dev"
mkdir -p "$OUT"
WAIT_START="${WAIT_START:-14}"

sh_() { "$ADB" -s "$SER" shell "$@"; }

for arm in "old:0:" "b06:0.06:" "b30:0.30:" "badanchor:0.06:0.5"; do
  TAG="${arm%%:*}"; rest="${arm#*:}"; BAND="${rest%%:*}"; ANCHOR="${rest#*:}"
  if [ -n "$ANCHOR" ]; then
    sh_ "echo $ANCHOR > $DEV_APP/seamanchor"; echo "  [$TAG] seamanchor=$ANCHOR"
  else
    sh_ "rm -f $DEV_APP/seamanchor"
  fi
  sh_ "echo $BAND > $DEV_APP/seamband"
  sh_ "chown \$(stat -c %u $DEV_APP):\$(stat -c %g $DEV_APP) $DEV_APP/seamband"
  sh_ "am force-stop $PKG" || true
  sleep 2
  sh_ "monkey -p $PKG -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1 || true
  sleep 6
  sh_ "input tap 540 1300" >/dev/null 2>&1 || true      # 点沙漏 = 开始
  sleep "$WAIT_START"
  "$ADB" -s "$SER" exec-out screencap -p > "$OUT/$TAG.png"
  echo "  [$TAG] band=$BAND anchor=${ANCHOR:-默认} -> $TAG.png"
done
sh_ "rm -f $DEV_APP/seamband $DEV_APP/seamanchor"
echo "  截图 -> benchmark_logs/_vid/seam_dev/*.png"
