#!/bin/bash
# 纹理上传值多少: **"每次调用的固定开销" vs "每字节带宽"** —— 四臂单变量对照(2026-10-07)。
#
# ## 为什么要单独量这个
#
# 先前那次 `blit.off` 消融把**整条 `blit_buffer` 调用**(调用 + 字节)一起摘掉了 ⇒
# 手上只有"上传总共值 0.12~0.31ms", **分不开两笔钱**。而两者的结论**相反**:
#   per-call  ⇒ 做纹理图集(13 次调用并成 ~4 次)能把这笔钱拿回来;
#   per-byte  ⇒ 图集要传整行 padding, **反而更慢**, 该原地不动。
# 图集要改 4 个 shader(~2h), 所以**先花 12 分钟量清楚再决定**。
#
# ## 四臂
#
#   A  基线          无标记文件
#   B  blit.rep=9    同一次上传**重复 9 遍**(载荷一字不变) ⇒ (B−A)/8 = **每次调用的价**
#   C  blit.wide     每次上传传**满整条纹理**(颈部从 ~36 纹素涨到 2048, **57×**) ⇒
#                    C−A = **每字节的价**; 调用次数与 A **完全相同** ⇒ 干净的单变量
#   A2 基线(重跑)    对照漂移 —— 项目纪律: 只跑改动侧 / 只跑一轮都不出结论
#
# ⚠️ 三个旋钮**只影响纹理新鲜度**, 不动帧循环其余部分 ⇒ 与 `blit.off` 同理,
#    **只能用来量钱, 不许当出货配置**。
# ⚠️ 每个臂跑完都要在日志里核对 `blit_rep=` / `blit_wide=` —— 标记文件没被读到的话,
#    这一臂就是**空转**(量的还是基线), 必须当场翻红而不是当成"没差别"。
#
# 用法: bash tools/_blit_probe_arms.sh          # 四臂, 约 12 分钟
set -u
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
SER="${SER:-127.0.0.1:16384}"
DEV_APP=/data/data/org.shalou.hourglass/files/app
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/benchmark_logs/blitprobe"
mkdir -p "$OUT"

markers_clear() {
  "$ADB" -s "$SER" shell "rm -f $DEV_APP/blit.rep $DEV_APP/blit.wide $DEV_APP/blit.off" >/dev/null
}

arm() {                                # $1=tag  $2=base|rep|wide
  local tag="$1" mode="$2"
  markers_clear
  case "$mode" in
    rep)  "$ADB" -s "$SER" shell "echo 9 > $DEV_APP/blit.rep" >/dev/null;;
    wide) "$ADB" -s "$SER" shell "touch $DEV_APP/blit.wide" >/dev/null;;
  esac
  local want_rep=1 want_wide=0
  [ "$mode" = rep ] && want_rep=9
  [ "$mode" = wide ] && want_wide=1

  echo "=== 臂 $tag ($mode) 开始 $(date +%H:%M:%S) ==="
  bash "$ROOT/tools/_one_bench.sh" "$tag" > "$OUT/$tag.run.log" 2>&1
  local rc=$?
  local f
  f=$(ls -t "$ROOT/benchmark_logs/dev/${tag}__"* 2>/dev/null | head -1)
  if [ -z "$f" ]; then echo "!! 臂 $tag 没拉到日志(rc=$rc)"; tail -5 "$OUT/$tag.run.log"; return 1; fi
  cp "$f" "$OUT/$tag.txt"

  # ★ 信任链: 标记文件**必须真的被 app 读到**, 否则这一臂是空转
  local got_rep got_wide
  got_rep=$(grep -m1 -o 'blit_rep=[0-9]*' "$OUT/$tag.txt" | cut -d= -f2)
  got_wide=$(grep -m1 -o 'blit_wide=[0-9]*' "$OUT/$tag.txt" | cut -d= -f2)
  if [ "$got_rep" != "$want_rep" ] || [ "$got_wide" != "$want_wide" ]; then
    echo "!! 臂 $tag **旋钮没生效**: 期望 rep=$want_rep wide=$want_wide, 日志里 rep=${got_rep:-无} wide=${got_wide:-无}"
    echo "   ⇒ 这一臂量的是基线, **作废**"
    return 1
  fi
  echo "   旋钮确认 rep=$got_rep wide=$got_wide ✓"
  grep -E "诊断耗时" "$OUT/$tag.txt" | sed 's/^/   /'
  markers_clear
  return 0
}

rc=0
for spec in "A:base" "B:rep" "C:wide" "A2:base"; do
  arm "${spec%%:*}" "${spec##*:}" || rc=1
done
markers_clear
echo "=== 四臂结束 rc=$rc  $(date +%H:%M:%S) ==="
exit $rc
