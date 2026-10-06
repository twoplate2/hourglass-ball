#!/bin/bash
# **闪光层"回退路径"的负对照** —— 强制装配失败, 证明退回逐 `Rectangle` 后画面**逐像素不变**。
#
# ## 为什么必须有它(2026-10-07 实测抓到两个真 bug)
#
# `tune_` 那套守卫只能证"装配成功时画得对"。而装配会失败(着色器编译不过、GL 能力不足),
# 那时走的是**另一条分支**, 没人走过就不知道它还能不能画:
#
# 1. 回滚时漏把 `_flare_group` 放回画布 ⇒ 闪光**无声地整层消失**(不崩、不报错);
# 2. 无条件放回去 ⇒ 同一个 group 在画布列表里挂**两份** ⇒ 每帧 apply 两次
#    ⇒ 半透明闪光被混合两遍、**整体变亮 +2~3 级**(14 个像素)。
#
# 两条都只有"**逼它走一遍回退、再与正常路径逐像素比**"才看得见。
#
# 用法: bash tools/_probe_flare_fallback.sh
# 退出码 0 = 回退路径与逐 Rectangle 路径**逐像素相同**。
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
RUN_PY="${PY:-python}"
COMMON="--steady-period 15 --steady-frames 30"
export HG_SPLASH_RENDERER=batch HG_NECK_RENDERER=batch HG_FLOW_RENDERER=texture

rm -rf benchmark_logs/flow_visual_fbOK benchmark_logs/flow_visual_fbFAIL
HG_FLARE_RENDERER=rect $RUN_PY tools/inspect_flow.py --label fbOK $COMMON >/dev/null 2>&1
OUT=$(HG_FLARE_RENDERER=batch HG_FLARE_FORCE_FAIL=1 \
      $RUN_PY tools/inspect_flow.py --label fbFAIL $COMMON 2>&1 || true)
echo "$OUT" | grep -a "flare batch failed" | head -1 || {
  echo "!! 没看到强制失败的信息 —— 说明这段根本没走到回退, 本次对照不算数"; exit 2; }

set +e
PYTHONIOENCODING=utf-8 $RUN_PY tools/_pixdiff.py \
    benchmark_logs/flow_visual_fbOK benchmark_logs/flow_visual_fbFAIL
rc=$?
set -e
[ $rc -eq 0 ] && echo "==> 回退路径 OK(与逐 Rectangle 逐像素相同)" \
             || echo "!! 回退路径有差异(见上) —— 兜底那条路是**真的会画的**, 别只看它不崩"
exit $rc
