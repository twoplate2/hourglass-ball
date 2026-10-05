#!/bin/bash
# 找茬用的**串行化**模拟器驱动: 一条命令跑一个场景(可含多步), 全程持锁。
#
# 用法:
#   tools/_review_emu.sh <输出目录> <前缀> <步骤> [<步骤>...]
#
# 步骤:
#   launch              强制重启 app 到干净状态(清 config 后启动)
#   launchkeep          重启但保留 config
#   tap:<名字>          点命名控件(见下方坐标表)
#   tapxy:<x>:<y>       点任意坐标(设备真实像素 1080x2400)
#   long:<名字>         长按 3.5s(版本号那块=隐藏菜单)
#   sleep:<秒>          等待(可用小数)
#   shot:<名字>         截一张 → <输出目录>/<前缀>_<名字>.png
#   seq:<名字>:<张数>:<间隔秒>   连拍(看动画必须看连续帧)
#   key:<keyevent>      例如 key:4 = 返回
#   list                只打印坐标表
#
# 为什么必须持锁: 多个进程同时驱动同一台模拟器会互相 force-stop / 点击,
# 拿到的"本轮"截图其实是对方那次运行产生的(A/B 脚本踩过, 见其护栏 1)。
set -u
export MSYS_NO_PATHCONV=1
# ⚠️ 整机 offline(端口 16384 不再监听)时 `adb connect` / `kill-server` 都无效,
#    r6-1号 实测只有 MuMuManager 能救回来:
#      "/c/Program Files/Netease/MuMu/nx_main/MuMuManager.exe" adb -v 0 "devices"
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
PKG=org.shalou.hourglass
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LOCKDIR="$ROOT/_review/.lock"

# 设备真实像素坐标(1080x2400, density 280) —— 改弹窗布局/按钮顺序必须重新量
declare -A NAME=(
  # ⚠️ 色块行 2026-10-04 修正: 原表整体偏左(最多 200px), 是照缩放图目测的。
  # 现值为 y=70 整行扫描出的真实按钮边界 (14..183 / 190..359 / 367..536 /
  # 543..712 / 720..889 / 896..1065), 中心即下值 —— 由 1 号评审发现、主持人独立复测确认。
  [金沙]="98 70"    [红沙]="274 70"   [蓝沙]="451 70"
  [绿沙]="627 70"   [紫沙]="804 70"   [黑沙]="980 70"
  [周期]="68 2323"  [音效]="235 2323" [版本]="539 2323"
  [开始]="852 2323" [重置]="994 2323"
  [画布上]="540 700" [画布中]="540 1400" [画布下]="540 2000"
  # 周期弹窗(2026-10-04 用 smoke_durpopup.png 逐色块量出, 不是目测)
  [基础1秒]="210 1027" [基础10秒]="430 1027" [基础1分]="650 1027" [基础10分]="870 1027"
  [倍1]="189 1168" [倍2]="364 1168" [倍3]="539 1168" [倍5]="715 1168" [倍10]="891 1168"
  [倍20]="189 1252" [倍30]="364 1252" [倍50]="539 1252" [倍70]="715 1252" [倍100]="891 1252"
  [周期确定]="761 1512" [周期取消]="319 1512"
  # 音效弹窗(2026-10-04 用 snd_popup.png 量)
  [音沙沙]="244 1129" [音水流]="540 1129" [音风]="836 1129"
  [音钟表]="318 1241" [音无]="728 1241"   [音效确定]="540 1363"
  [菜单性能]="536 1392"  [菜单开始]="220 1426"
)
list_names() { for k in "${!NAME[@]}"; do echo "  $k -> ${NAME[$k]}"; done | sort; }
if [ "${1:-}" = "list" ]; then echo "命名控件(设备像素):"; list_names; exit 0; fi

OUT="${1:?用法: $0 <输出目录> <前缀> <步骤>... }"
TAG="${2:?缺少前缀}"
shift 2
mkdir -p "$OUT"

# ---- 持锁(等待式 + 超时抢锁): 每 2 秒重试 ----
# ⚠️ 必须有"抢锁"这一手: 某个评审被超时杀掉 / Ctrl-C 时 trap 不一定会跑, 锁会永久留着,
#    后面每个人都白等 10 分钟然后放弃 —— 12 小时的循环经不起这个。
#    判据用锁目录的 mtime(一个场景最长也就两分钟), 超过 LOCK_STALE 秒就认定是僵尸锁。
LOCK_STALE="${LOCK_STALE:-420}"
waited=0
until mkdir "$LOCKDIR" 2>/dev/null; do
  now=$(date +%s)
  lt=$(stat -c %Y "$LOCKDIR" 2>/dev/null || echo "$now")
  if [ $((now - lt)) -ge "$LOCK_STALE" ]; then
    echo "!! 锁已闲置 $((now - lt))s (>${LOCK_STALE}), 判定为僵尸锁, 夺锁"
    rmdir "$LOCKDIR" 2>/dev/null || rm -f "$LOCKDIR" 2>/dev/null
    continue
  fi
  sleep 2; waited=$((waited+2))
  if [ $waited -ge 900 ]; then
    echo "!! 等锁超过 900s, 放弃。持锁者可能卡住; 确认后 rm -rf $LOCKDIR" >&2; exit 1
  fi
done
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT INT TERM
[ $waited -gt 0 ] && echo "(等锁 ${waited}s)"
# 长场景要定期更新 mtime, 否则会被别人当僵尸锁抢走
touch "$LOCKDIR"

DEV="127.0.0.1:16384"
"$ADB" -s "$DEV" get-state >/dev/null 2>&1 || { echo "!! 模拟器不在线"; exit 1; }

shot() {  # $1=名字
  touch "$LOCKDIR" 2>/dev/null      # ⚠️ 每次截图都刷新锁: 单个 seq 连拍要跑几分钟,
  #   只在每个 step 开头 touch 会让长场景被别的评审当僵尸锁抢走(3号实测踩到)
  "$ADB" -s "$DEV" exec-out screencap -p > "$OUT/${TAG}_$1.png"
  local n; n=$(stat -c %s "$OUT/${TAG}_$1.png" 2>/dev/null || echo 0)
  if [ "$n" -lt 20000 ]; then echo "!! 截图可疑($n 字节): $1"; return 1; fi
  echo "  shot $1  ($n bytes)"
}
launch() {  # $1=1 则清 config
  "$ADB" -s "$DEV" shell am force-stop $PKG >/dev/null 2>&1
  [ "${1:-0}" = "1" ] && "$ADB" -s "$DEV" shell run-as $PKG rm -f files/.hourglass_config.json >/dev/null 2>&1
  sleep 1
  "$ADB" -s "$DEV" shell monkey -p $PKG -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
  sleep 9
}
for step in "$@"; do
  touch "$LOCKDIR" 2>/dev/null
  case "$step" in
    list) list_names;;
    launch)     echo "launch(清配置)"; launch 1;;
    launchkeep) echo "launch";        launch 0;;
    sleep:*)
      # ⚠️ 修: 原实现 `[ "${_i%.*}" -lt "${_t%.*}" ]` 是**整数**判定 ——
      #    `sleep:0.2` 的 ${_t%.*} = "0" ⇒ 循环体一次都不进 ⇒ **一点没睡**;
      #    `sleep:1` 反而因为先睡 2s 再判 ⇒ 实际睡 2s。(r4-2号 实测抓到, 已污染过若干"亚秒"
      #    场景 —— 凡是依赖 sleep 精确时长的历史结论都要按"实际比标称长"重读。)
      # 现在: ≤5s 直接一次 sleep(小数可用); >5s 分块并在期间刷新锁。
      _t="${step#sleep:}"
      if awk -v b="$_t" 'BEGIN{exit !(b <= 5)}'; then
        sleep "$_t"
      else
        _i=0
        while awk -v a="$_i" -v b="$_t" 'BEGIN{exit !(a < b)}'; do
          touch "$LOCKDIR" 2>/dev/null
          sleep 2; _i=$((_i + 2))
        done
      fi
      touch "$LOCKDIR" 2>/dev/null;;
    key:*)      "$ADB" -s "$DEV" shell input keyevent "${step#key:}";;
    tap:*)
      n="${step#tap:}"; c="${NAME[$n]:-}"
      [ -z "$c" ] && { echo "!! 未知控件 $n"; exit 1; }
      echo "tap $n ($c)"; "$ADB" -s "$DEV" shell input tap $c; sleep 0.5;;
    tapxy:*)
      IFS=: read -r _ x y <<<"$step"; echo "tap ($x,$y)"
      "$ADB" -s "$DEV" shell input tap "$x" "$y"; sleep 0.5;;
    long:*)
      n="${step#long:}"; c="${NAME[$n]:-}"; [ -z "$c" ] && { echo "!! 未知控件 $n"; exit 1; }
      set -- $c; echo "long $n"
      "$ADB" -s "$DEV" shell input swipe "$1" "$2" "$1" "$(( $2 + 1 ))" 3500; sleep 0.8;;
    shot:*)     shot "${step#shot:}";;
    seq:*)
      IFS=: read -r _ n cnt iv <<<"$step"
      for i in $(seq 1 "$cnt"); do shot "${n}_$i"; sleep "$iv"; done;;
    *) echo "!! 未知步骤 $step"; exit 1;;
  esac
done
echo "done -> $OUT"
