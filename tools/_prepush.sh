#!/bin/bash
# 出货前的本地闸门（2026-10-12 立）。**任何一道红 ⇒ 不许 push。**
#
# 为什么要有它：今天连着三次把 main.py / flow_numpy.py / sand_flow_material.py 改坏，
# 而 `pyflakes` **每次都是绿的** —— 它查不出"少了一个方法""改了个属性名""uniform 没人喂"
# 这类东西。三次里两次是「跑起来才发现」、一次是「渲染出来才发现」。
# ⇒ 唯一可靠的入口闸门是**真的把 app 跑起来渲一帧**，并检查：
#     ① 没有 Traceback ② 材质 context 真起来了（不是静默兜底）③ 画面与参照臂的差落在预期内。
#
# 用法: bash tools/_prepush.sh [参照臂标签]      # 默认参照臂 = sieveoff_default
set -u
cd "$(dirname "$0")/.."
FAIL=0
REF="${1:-sieveoff_default}"

echo "== 1/4 pyflakes（CI 第三道早退闸门的同款检查）=="
if python -m pyflakes main.py frame_benchmark.py app_version.py tools/*.py 2>/dev/null | grep -qi "undefined name"; then
  echo "  ✗ 有未定义名 —— 不许 push"; python -m pyflakes main.py app_version.py tools/*.py 2>/dev/null | grep -i "undefined name" | head -5; FAIL=1
else
  echo "  ✓ 空"
fi

echo "== 2/4 烟测：真的把 app 跑起来、渲一帧（这是唯一能抓'少方法/uniform 没喂'的闸门）=="
OUT=$(PYTHONIOENCODING=utf-8 timeout 300 python tools/inspect_flow.py --label _smoke \
        --pixels 1904,2890 --texture-flow --steady-period 50 --window 8.77,0.02,1 2>&1)
if echo "$OUT" | grep -qi "traceback"; then
  echo "  ✗ 有 Traceback —— 不许 push"; echo "$OUT" | grep -iA6 traceback | head -20; FAIL=1
else
  echo "  ✓ 无异常"
fi
if echo "$OUT" | grep -q "GPU sand flow active"; then
  echo "  ✓ 材质 context 起来了"
else
  echo "  ✗ 材质**没起来**（静默兜底：画面会是另一套东西）"; echo "$OUT" | tail -6; FAIL=1
fi

echo "== 3/4 与参照臂逐像素（版本号区由 _pixdiff 屏蔽）=="
if [ -d "benchmark_logs/flow_visual_${REF}" ]; then
  python tools/_pixdiff.py "benchmark_logs/flow_visual__smoke" "benchmark_logs/flow_visual_${REF}" 2>&1 | tail -4
else
  echo "  ✗ 找不到参照臂 benchmark_logs/flow_visual_${REF} —— **本项不作数**（别当它过了）"; FAIL=1
fi

echo "== 4/4 交付清单（人工逐条，缺一不许 push；照 design/验收标准.html §3）=="
cat <<'EOF'
  [ ] 改前/改后并排图 已发给用户（同时刻、同口径、1:1、不缩放）
  [ ] 机械判据：每条附**命令 + 原始输出**，标明证据等级 E1/E2/E3
  [ ] 门槛两侧标定过（已知好 / 已知坏各跑一遍，分得开）
  [ ] 关旋钮 ⇒ 逐像素回参照（本版若声称"不影响画面"）
  [ ] 设备 1:1 实测（MuMu 直推 + 截图）
  [ ] 未验证项 / 没覆盖的档位 如实列出
  [ ] 本版只动**一处**
EOF
[ "$FAIL" = 0 ] && echo "== 入口闸门全过；清单没勾完仍不许 push ==" || echo "== ✗ 有红项 —— 不许 push =="
exit $FAIL
