#!/bin/bash
# 1号 attack: run a matrix of single-variable renders at the tablet caliber.
cd /e/AI_Tools/other/shalou_claude/pc/apk
run () {
  local label="$1"; shift
  rm -rf "benchmark_logs/flow_visual_$label"
  env "$@" python tools/inspect_flow.py --label "$label" --pixels 1904,2890 \
      --steady-period 15 --window 7.40,0.04,13 >/tmp/atk_$label.log 2>&1
  echo "done $label -> $(ls benchmark_logs/flow_visual_$label/*.png | wc -l) png"
}
case "$1" in
  K8)
  run atk_base
  run atk_base2
  run atk_alpha50 HG_SAND_ALPHA=0.5
  run atk_edge0 HG_SAND_EDGE=0
  run atk_core99 HG_SAND_CORE=0.99
  run atk_bite00 HG_SAND_BITE=0.0
  run atk_hole50 HG_HOLE_TH=0.5
  run atk_noho HG_HOLE_TH=-1
  run atk_noho_a50 HG_HOLE_TH=-1 HG_SAND_ALPHA=0.5
  run atk_noho_e0 HG_HOLE_TH=-1 HG_SAND_EDGE=0
  run atk_grow08 HG_HOLE_GROW=0.8
  run atk_g08_a50 HG_HOLE_GROW=0.8 HG_SAND_ALPHA=0.5
  run atk_g08_e0 HG_HOLE_GROW=0.8 HG_SAND_EDGE=0
  ;;
  K7)
  run atk_sp0 HG_STREAM_SPREAD=0
  run atk_sp2 HG_STREAM_SPREAD=2.0
  run atk_free0 HG_NECK_FREE=0
  run atk_free0_sp0 HG_NECK_FREE=0 HG_STREAM_SPREAD=0
  run atk_trail02 HG_TRAIL_SCALE=0.2
  ;;
esac
