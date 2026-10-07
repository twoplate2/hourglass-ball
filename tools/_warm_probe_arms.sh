#!/bin/bash
# 批处理块**预热**值多少: 同一份代码, 只切 `warm.off` 标记文件 —— 两臂**交替**各 2 轮。
#
# ## 为什么量这个
#
# 用户设备(Lenovo TB323FU, 120Hz)的 log 显示: **每一档的最慢帧都落在沙柱注满那一刻**
# (`_neck_fill_time`), 那一帧粒子还很少(2~58 颗)而 `图元` 高达 **23~44ms**(稳态 2.9ms):
#
#   | 档  | 最慢帧        | 粒子 | 图元   | 该帧 flow_chunks |
#   |-----|---------------|------|--------|------------------|
#   | 1s  | t=0.156s 48.9 | 58   | 43.88  | 0 → 7            |
#   | 5s  | t=0.27s  25.3 | 31   | 23.14  | —                |
#   | 15s | t=0.25s  35.6 | 5    | 32.27  | —                |
#
# 沙柱注满前**不出粒子** ⇒ 注满那一帧**所有桶同时**第一次拿到内容 ⇒ 那一帧在**建块**。
# `warm` 把建块挪到"没在跑"的闲帧里(见 `main.py:_warm_batches_step`)。
#
# ## 臂
#
#   A  warm.on    无标记文件(= 出货默认)
#   B  warm.off   `touch warm.off` ⇒ 退回"第一次有内容才建块"
#   A2/B2         各重跑一轮 —— **项目纪律: 只跑一轮 / 只跑一侧都不出结论**
#
# ⚠️ **标记文件必须自证被读到**: 每臂都核对日志里 `warm=` 与期望一致, 不一致当场作废
#   (安卓读不到宿主环境变量, 这条是唯一能证明"这一臂不是空转"的证据)。
# ⚠️ 判据看 `图元` 那一栏与 `index_assigns`, **不是帧率** —— 这台模拟器的呈现节拍
#   锁在 60, 平均帧率是死指标(见 CLAUDE.md)。
#
# 用法: bash tools/_warm_probe_arms.sh        # 四臂, 约 8 分钟
set -u
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
SER="${SER:-127.0.0.1:16384}"
DEV_APP=/data/data/org.shalou.hourglass/files/app
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/benchmark_logs/warmprobe"
mkdir -p "$OUT"

arm() {                                    # $1=标签  $2=on|off
  local tag="$1" mode="$2"
  if [ "$mode" = off ]; then
    "$ADB" -s "$SER" shell "touch $DEV_APP/warm.off" >/dev/null
    local want=0
  else
    "$ADB" -s "$SER" shell "rm -f $DEV_APP/warm.off" >/dev/null
    local want=1
  fi
  echo "=== 臂 $tag (warm=$mode) 开始 $(date +%H:%M:%S) ==="
  bash "$ROOT/tools/_one_bench.sh" "$tag" > "$OUT/$tag.run.log" 2>&1
  local rc=$?
  local f
  f=$(ls -t "$ROOT/benchmark_logs/dev/${tag}__"* 2>/dev/null | head -1)
  if [ -z "$f" ]; then echo "!! 臂 $tag 没拉到日志(rc=$rc)"; tail -5 "$OUT/$tag.run.log"; return 1; fi
  cp "$f" "$OUT/$tag.txt"
  local got
  got=$(grep -m1 -o 'warm=[0-9]*' "$OUT/$tag.txt" | cut -d= -f2)
  if [ "${got:-无}" != "$want" ]; then
    echo "!! 臂 $tag **旋钮没生效**: 期望 warm=$want, 日志里 warm=${got:-无} ⇒ 作废"
    return 1
  fi
  echo "   旋钮确认 warm=$got ✓"
  return 0
}

for r in 1 2; do
  arm "w${r}on"  on
  arm "w${r}off" off
done
"$ADB" -s "$SER" shell "rm -f $DEV_APP/warm.off" >/dev/null
echo ""
echo "四臂跑完 —— 分析: python tools/_warm_probe_report.py"
