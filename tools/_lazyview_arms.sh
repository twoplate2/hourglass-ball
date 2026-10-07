#!/bin/bash
# 惰性 list 快照那条改动的设备 A/B —— **交替四臂**(2026-10-07)。
#
# 改的是什么: `TextureFlowBatch.update` 开头**无条件**读 `view.x/y/vy/tl` 四个属性,
# 而它们是 `_FlowView` 的**惰性属性** —— 第一次读就 `tolist()` 一整份(n≈1500)。
# 安卓走 numpy 分支, 那四个 list **一个都不读**; 融合路径更是只对**空桶**调 `update`。
# ⇒ 每帧白建 4×~1500 个 float。挪进唯一读它们的标量分支(纯删无用功)。
#
# 四臂(交替, 不是 A…A 然后 B…B —— 这台机器的基线自己会漂):
#   A  基线   lazyview.off 存在  = 旧行为(无条件读)
#   B  改动   无标记文件          = 新行为(读挪进标量分支)
#   A2 基线   lazyview.off 存在
#   B2 改动   无标记文件
# 判据 = **B 与 B2 一致地低于 A 与 A2**, 且差值 > 两者组内极差。
#
# ⚠️ 每臂都要在日志里回读 `lazy_view_off=` —— 标记文件没被读到的话那一臂是空转,
#    会被误读成"没差别"。**没对上就当场作废, 不许进结论。**
#
# 用法: bash tools/_lazyview_arms.sh
set -u
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
SER="${SER:-127.0.0.1:16384}"
DEV_APP=/data/data/org.shalou.hourglass/files/app
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/benchmark_logs/lazyview"
mkdir -p "$OUT"

markers_clear() {
  "$ADB" -s "$SER" shell "rm -f $DEV_APP/lazyview.off $DEV_APP/blit.rep $DEV_APP/blit.wide $DEV_APP/blit.off" >/dev/null
}

arm() {                                # $1=tag  $2=old|new
  local tag="$1" mode="$2"
  markers_clear
  local want=0
  if [ "$mode" = old ]; then
    "$ADB" -s "$SER" shell "echo 1 > $DEV_APP/lazyview.off" >/dev/null
    want=1
  fi
  echo "=== 臂 $tag ($mode) 开始 $(date +%H:%M:%S) ==="
  bash "$ROOT/tools/_one_bench.sh" "$tag" > "$OUT/$tag.run.log" 2>&1
  local f
  f=$(ls -t "$ROOT/benchmark_logs/dev/${tag}__"* 2>/dev/null | head -1)
  if [ -z "$f" ]; then echo "!! 臂 $tag 没拉到日志"; tail -4 "$OUT/$tag.run.log"; return 1; fi
  cp "$f" "$OUT/$tag.txt"
  local got
  got=$(grep -m1 -o 'lazy_view_off=[0-9]*' "$OUT/$tag.txt" | cut -d= -f2)
  if [ "$got" != "$want" ]; then
    echo "!! 臂 $tag **旋钮没生效**: 期望 lazy_view_off=$want, 日志里 ${got:-无} ⇒ 本臂作废"
    return 1
  fi
  echo "   旋钮确认 lazy_view_off=$got ✓"
  grep -E "诊断耗时" "$OUT/$tag.txt" | sed 's/^/   /'
  markers_clear
  return 0
}

rc=0
for spec in "L1:old" "L2:new" "L3:old" "L4:new"; do
  arm "${spec%%:*}" "${spec##*:}" || rc=1
done
markers_clear
echo "=== 结束 rc=$rc $(date +%H:%M:%S) ==="
bash "$ROOT/tools/_dev_lock.sh" release >/dev/null 2>&1 || true
exit $rc
