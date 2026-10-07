#!/bin/bash
# **飞溅存活上限**值多少: 同一份代码, 只切 `splashmax` 标记文件 —— 两臂交替各 2 轮。
#
# ## 为什么量它(而不是猜)
#
# 用户平板 165Hz 的逐帧 CSV 里, 重帧(p95+)与中位帧的**沙流粒子数一样**(1877 vs 1887),
# 而**飞溅多 49%**(1551 vs 1045) —— 尾部成本由飞溅层驱动。而 165Hz 的预算是 6.06ms,
# 三栏和 p50 已 4.04 / p90 6.55ms ⇒ 飞溅一涨就把帧顶过 vsync(掉一格 = 12.1ms)。
#
# ## 臂
#
#   A  不限制(`rm splashmax`)        = 出货默认
#   B  上限 1200(`echo 1200 > splashmax`)
#   A2/B2                            各重跑一轮(项目纪律: 单轮/单侧都不出结论)
#
# 判据: 四栏 ms(`图元`/`物理`)与**逐帧 CSV 里 frame_ms > 9ms 的帧数**(165Hz 上跨过 1.5 格),
# **不是平均帧率**(这台模拟器的呈现节拍锁在 60, 平均值是死指标)。
#
# ⚠️ 标记文件必须自证被读到: 每臂都核对日志里 `splash_max=` 与期望一致, 不一致当场作废。
#
# 用法: bash tools/_splash_max_arms.sh [上限]     # 默认 1200
set -u
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
SER="${SER:-127.0.0.1:16384}"
DEV_APP=/data/data/org.shalou.hourglass/files/app
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/benchmark_logs/splashmax"
mkdir -p "$OUT"
LIM="${1:-1200}"

arm() {                                    # $1=标签  $2=上限(0=不限制)
  local tag="$1" lim="$2"
  if [ "$lim" = "0" ]; then
    "$ADB" -s "$SER" shell "rm -f $DEV_APP/splashmax" >/dev/null
  else
    "$ADB" -s "$SER" shell "echo $lim > $DEV_APP/splashmax" >/dev/null
  fi
  echo "=== 臂 $tag (splash_max=$lim) 开始 $(date +%H:%M:%S) ==="
  bash "$ROOT/tools/_one_bench.sh" "$tag" > "$OUT/$tag.run.log" 2>&1
  local rc=$?
  local f
  f=$(ls -t "$ROOT/benchmark_logs/dev/${tag}__"* 2>/dev/null | head -1)
  if [ -z "$f" ]; then echo "!! 臂 $tag 没拉到日志(rc=$rc)"; tail -5 "$OUT/$tag.run.log"; return 1; fi
  cp "$f" "$OUT/$tag.txt"
  local got
  got=$(grep -m1 -o 'splash_max=[0-9]*' "$OUT/$tag.txt" | cut -d= -f2)
  if [ "${got:-无}" != "$lim" ]; then
    echo "!! 臂 $tag **旋钮没生效**: 期望 splash_max=$lim, 日志里 ${got:-无} ⇒ 作废"
    return 1
  fi
  echo "   旋钮确认 splash_max=$got ✓"
  return 0
}

for r in 1 2; do
  arm "s${r}off" 0
  arm "s${r}on" "$LIM"
done
"$ADB" -s "$SER" shell "rm -f $DEV_APP/splashmax" >/dev/null
echo ""
echo "四臂跑完 —— 分析: python tools/_splash_max_report.py"
