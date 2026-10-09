#!/bin/bash
# 渲染某个 revision 的 inspect_flow 集, 并与指定基线目录比 period-* 一族
# 用法: bash tools/_bisect_render.sh <rev> <label>
set -e
REV="$1"; LABEL="$2"
WT="/tmp/hg_$LABEL"
rm -rf "$WT"
git worktree add -f "$WT" "$REV" >/dev/null 2>&1
mkdir -p "$WT/benchmark_logs"
( cd "$WT" && python tools/inspect_flow.py --label "$LABEL" ${RENDER_ARGS:---steady-period 15 --steady-frames 30} >/dev/null 2>&1 )
echo "RENDER_DONE $LABEL -> C:/Users/liangpan/AppData/Local/Temp/hg_$LABEL/benchmark_logs/flow_visual_$LABEL"
