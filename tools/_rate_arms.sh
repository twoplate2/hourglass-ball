#!/bin/bash
# 生成率(在途粒子数)的设备 A/B —— **交替四臂**(2026-10-07)。
#
# 为什么挑它: `物理` 与 `图元` **两栏都正比于在途粒子数**, 所以这是**唯一**能把两大栏
# 一起按百分比压下去的旋钮。实测这台机器上 **< 0.5ms 的改动量不出来**(lazy-view 那次
# 四臂交替, 差值是 0.00~0.10ms, 组内极差就有 0.19), 而按比例压粒子数能到 ~1ms 量级。
#
# 代价是沙流的**线密度(颗/px)**同比例下降 —— 那是观感, 量具在
# `tools/_probe_stream_ink.py`(设备实测 100% 行覆盖率的参照是 lin≈2.80, 当前 lin≈5.82)。
#
# 四臂交替(A B A B): 这台机器的基线自己会漂, 单对比 A→B 会把漂移算进效应里。
#
# 用法: bash tools/_rate_arms.sh [新rate=1200]
set -u
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
SER="${SER:-127.0.0.1:16384}"
DEV_APP=/data/data/org.shalou.hourglass/files/app
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/benchmark_logs/rate"
NEW="${1:-1200}"
mkdir -p "$OUT"

markers_clear() {
  "$ADB" -s "$SER" shell "rm -f $DEV_APP/flowrate $DEV_APP/lazyview.off $DEV_APP/blit.rep $DEV_APP/blit.wide $DEV_APP/blit.off" >/dev/null
}

arm() {                       # $1=tag  $2=想要的 flow_rate(1500=基线)
  local tag="$1" want="$2"
  markers_clear
  if [ "$want" != "1500" ]; then
    "$ADB" -s "$SER" shell "echo $want > $DEV_APP/flowrate" >/dev/null
  fi
  echo "=== 臂 $tag (rate=$want) 开始 $(date +%H:%M:%S) ==="
  bash "$ROOT/tools/_one_bench.sh" "$tag" > "$OUT/$tag.run.log" 2>&1
  local f
  f=$(ls -t "$ROOT/benchmark_logs/dev/${tag}__"* 2>/dev/null | head -1)
  if [ -z "$f" ]; then echo "!! 臂 $tag 没拉到日志"; tail -4 "$OUT/$tag.run.log"; return 1; fi
  cp "$f" "$OUT/$tag.txt"
  local got
  got=$(grep -m1 -o 'flow_rate=[0-9.]*' "$OUT/$tag.txt" | cut -d= -f2)
  # 日志里是 float(会打成 1200.0 / 1500.0)
  if [ "${got%.*}" != "$want" ]; then
    echo "!! 臂 $tag **旋钮没生效**: 期望 flow_rate=$want, 日志里 ${got:-无} ⇒ 本臂作废"
    return 1
  fi
  echo "   旋钮确认 flow_rate=$got ✓"
  grep -E "诊断耗时|存活粒子" "$OUT/$tag.txt" | sed 's/^/   /'
  markers_clear
  return 0
}

rc=0
for spec in "R1:1500" "R2:$NEW" "R3:1500" "R4:$NEW"; do
  arm "${spec%%:*}" "${spec##*:}" || rc=1
done
markers_clear
echo "=== 结束 rc=$rc $(date +%H:%M:%S) ==="
bash "$ROOT/tools/_dev_lock.sh" release >/dev/null 2>&1 || true
exit $rc
