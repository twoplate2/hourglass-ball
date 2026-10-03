#!/bin/bash
# 在 MuMu 模拟器上对两个 git 版本做**交替多轮**帧率 A/B。
#
# 用法:
#   tools/ab_device_benchmark.sh <revA> <revB> [轮数, 默认 3]
# 例:
#   tools/ab_device_benchmark.sh 7c8d094 94df8bc 3
#
# 产物: benchmark_logs/ab/<revA|revB>_r<n>__<时间戳>.txt
# 分析: python tools/analyze_ab.py benchmark_logs/ab
#
# ⚠️ 四条硬约束(都踩过坑, 别改):
#  1. 每个臂必须推**自己那一套**文件 —— 至少 main.py / app_version.py / frame_benchmark.py
#     / tools/flow_texture_experiment.py。最后这个最容易漏: 两版差 96 行(v1.2 收 particles
#     字典表, v1.21 收 view+indices 并行数组), 漏推会
#     `AttributeError: 'DictFlowView' object has no attribute 'use_np'` → **应用秒退**。
#  2. 两个臂各用**自己的** frame_benchmark.py: v1.2 读 len(widget.particles)(原生 list, O(1)),
#     v1.21 读 widget.pn(v1.2 没有 pn, 混用会崩)。
#  3. 存活检查**必须重试** —— 单次 adb 抖动会把还在跑的基准判死, 然后 force-stop 掐掉它。
#  4. 判定"应用死了"之前**先查有没有新日志落盘**(落盘即成功)。
#
# ⚠️ 单轮基准要 5–10 分钟(1s+5s+15s 三段, **全部跑完才落盘**); 15 秒档偶尔会挂,
#    本轮超时(默认 15 分钟)就记 `未产出` 并继续。
# ⚠️ 判据: 比 **1% low / p90**, **不比平均**(apk/README 记档: 平均帧率在本机非有效指标,
#    跑间 ±12%)。**中位差要大于组内极差才算可分辨。**
set -u

ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
PKG=org.shalou.hourglass
DEV_APP=/data/data/$PKG/files/app
DEV_LOGS=/sdcard/Android/data/$PKG/files/benchmark_logs
OUT=benchmark_logs/ab
WORK="${TMPDIR:-/tmp}/ab_work"
TIMEOUT_ITERS="${TIMEOUT_ITERS:-90}"      # × 10s = 15 分钟/臂

# 要推的文件(存在才推; 缺失的在设备上会被删掉)
FILES="main.py app_version.py frame_benchmark.py tools/flow_texture_experiment.py tools/flow_batch_experiment.py tools/flow_numpy.py"

REV_A="${1:?用法: $0 <revA> <revB> [轮数]}"
REV_B="${2:?用法: $0 <revA> <revB> [轮数]}"
ROUNDS="${3:-3}"

say() { echo "[$(date +%H:%M:%S)] $*"; }

proc_count() { "$ADB" shell 'ps -A 2>/dev/null | grep -c org.shalou.hourglass' 2>/dev/null | tr -d '\r' | head -1; }

alive() {   # 重试 3 次再判死
  local i c
  for i in 1 2 3; do
    c=$(proc_count); [ -n "$c" ] && [ "$c" != "0" ] && return 0
    sleep 2
  done
  return 1
}

extract() {  # $1=rev -> $WORK/$1/
  local rev="$1" d="$WORK/$rev" f
  rm -rf "$d"; mkdir -p "$d/tools"
  git show "$rev:main.py" > "$d/main.py" || return 1
  git show "$rev:app_version.py" > "$d/app_version.py" || return 1
  git show "$rev:frame_benchmark.py" > "$d/frame_benchmark.py" || return 1
  for f in flow_texture_experiment flow_batch_experiment flow_numpy; do
    git show "$rev:tools/$f.py" > "$d/tools/$f.py" 2>/dev/null || rm -f "$d/tools/$f.py"
  done
  say "  导出 $rev: $(grep -o '"[0-9.]*"' "$d/app_version.py" | head -1) tools=[$(ls "$d/tools" | tr '\n' ' ')]"
}

push_arm() {  # $1=rev
  local rev="$1" d="$WORK/$rev" f base
  for f in $FILES; do
    base=$(basename "$f")
    if [ -f "$d/$f" ]; then
      "$ADB" push "$d/$f" "/data/local/tmp/$base" >/dev/null 2>&1 || { say "  推送失败 $f"; return 1; }
    fi
  done
  "$ADB" shell "set -e
    cp /data/local/tmp/main.py            $DEV_APP/main.py
    cp /data/local/tmp/app_version.py     $DEV_APP/app_version.py
    cp /data/local/tmp/frame_benchmark.py $DEV_APP/frame_benchmark.py
    cp /data/local/tmp/flow_texture_experiment.py $DEV_APP/tools/flow_texture_experiment.py
    cp /data/local/tmp/flow_batch_experiment.py   $DEV_APP/tools/flow_batch_experiment.py
    if [ -f $d/tools/flow_numpy.py ]; then
      cp /data/local/tmp/flow_numpy.py $DEV_APP/tools/flow_numpy.py
      chown u0_a66:u0_a66 $DEV_APP/tools/flow_numpy.py; chmod 600 $DEV_APP/tools/flow_numpy.py
    else
      rm -f $DEV_APP/tools/flow_numpy.py
    fi
    chown u0_a66:u0_a66 $DEV_APP/main.py $DEV_APP/app_version.py $DEV_APP/frame_benchmark.py \
        $DEV_APP/tools/flow_texture_experiment.py $DEV_APP/tools/flow_batch_experiment.py
    chmod 600 $DEV_APP/main.py $DEV_APP/app_version.py $DEV_APP/frame_benchmark.py \
        $DEV_APP/tools/flow_texture_experiment.py $DEV_APP/tools/flow_batch_experiment.py
    rm -f $DEV_APP/__pycache__/*.pyc $DEV_APP/tools/__pycache__/*.pyc" >/dev/null 2>&1
}

dump_logcat() { "$ADB" logcat -d > "$OUT/logcat_$1.txt" 2>&1; say "  logcat -> $OUT/logcat_$1.txt"; }

run_once() {  # $1=rev $2=round
  local rev="$1" round="$2" tag="$1_r$2" prev now i
  say "=== $tag ==="
  push_arm "$rev" || return 1
  "$ADB" shell am force-stop $PKG >/dev/null 2>&1; sleep 2
  "$ADB" shell monkey -p $PKG -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
  sleep 16
  alive || { say "  !! 启动后秒退"; dump_logcat "$tag"; return 1; }
  say "  存活 ✓"
  prev=$("$ADB" shell "ls -t $DEV_LOGS/ 2>/dev/null | head -1" | tr -d '\r')
  "$ADB" shell input swipe 546 2328 547 2328 3500   # 长按 3.5s 触发基准
  sleep 3
  "$ADB" shell input tap 220 1426                   # 点「开始测试」
  say "  基准启动, 轮询落盘"
  for i in $(seq 1 "$TIMEOUT_ITERS"); do
    now=$("$ADB" shell "ls -t $DEV_LOGS/ 2>/dev/null | head -1" | tr -d '\r')
    [ -n "$now" ] && [ "$now" != "$prev" ] && break     # 规则 4: 落盘优先于判死
    if [ $((i % 6)) -eq 0 ] && ! alive; then
      say "  !! 基准途中应用消失(~$((i*10))s)"; dump_logcat "$tag"; return 1
    fi
    sleep 10
  done
  [ -z "$now" ] || [ "$now" = "$prev" ] && { say "  !! 超时"; dump_logcat "$tag"; return 1; }
  "$ADB" pull "$DEV_LOGS/$now" "$OUT/${tag}__${now}" >/dev/null 2>&1
  say "  => $OUT/${tag}__${now} ($(wc -c < "$OUT/${tag}__${now}") bytes)"
}

mkdir -p "$OUT" "$WORK"
for r in $(seq 1 "$ROUNDS"); do
  for rev in "$REV_A" "$REV_B"; do
    extract "$rev" >/dev/null
    run_once "$rev" "$r" || say "!!! $rev r$r 未产出"
  done
done
say "=========== 全部完成 ==========="
ls -l "$OUT"
echo
echo "下一步: python tools/analyze_ab.py $OUT"
