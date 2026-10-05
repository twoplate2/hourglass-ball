#!/bin/bash
# 推送闸门: **只有"失败清单没有新增"才允许推**。
#
# ⚠️ 为什么写成脚本而不是命令行里一段 `$(comm -13 a b)`:
#    2026-10-05 真踩过 —— `comm` 的输入文件不存在时它报错到 stderr、`$(...)` 取到**空串**,
#    于是判断 `[ -z "$(...)" ]` 成立, 打印"无新增失败 ✅"并放行。
#    **输入缺失被当成了"没有新增失败"。** 这条纪律就是 `QA_RULES.md` 里那句
#    「'我跑了脚本'不等于 E1」的反面: 脚本必须能在**自己没跑成**的时候说不。
#    所以这里每一处失败路径都是 `exit 1`, 没有"默认放行"。
#
# 用法: tools/_gate_push.sh <旧失败清单(一行一条)> <本次闸门输出>
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE="${1:-}"; OUT="${2:-}"
if [ -z "$BASE" ] || [ -z "$OUT" ]; then
  echo "闸门: 用法 tools/_gate_push.sh <旧失败清单> <本次闸门输出> ❌"; exit 1
fi
for f in "$BASE" "$OUT"; do
  [ -s "$f" ] || { echo "闸门: 输入缺失或为空 -> $f ❌ 拒绝推送"; exit 1; }
done

# 闸门必须**真的跑到结尾**。两种正常结尾: 全绿打 `All checks passed`, 有红打 `FAILED: [...]`。
# ⚠️ **"没有 FAILED 行" 恰恰可能是全绿** —— 第一版把它一律当成"没跑完",
#    于是修好之后全绿那一轮**反而被自己拦下**(实测)。判"跑没跑完"要用**完成标记**,
#    不能用"有没有报错"。
if ! grep -qE '^(All checks passed|FAILED: \[)' "$OUT"; then
  echo "闸门: 输出里既没有 'All checks passed' 也没有 'FAILED: [...]'"
  echo "      —— 说明闸门没跑到结尾 ❌ 拒绝推送"; exit 1
fi
sed -n 's/^FAILED: \[\(.*\)\]$/\1/p' "$OUT" | tail -1 | tr ',' '\n' \
  | sed "s/^[[:space:]]*'//; s/'[[:space:]]*$//" \
  | sed '/^[[:space:]]*$/d' | sort -u > /tmp/_hg_cur.txt
sort -u "$BASE" | sed '/^[[:space:]]*$/d' > /tmp/_hg_prev.txt
echo "旧失败 $(wc -l < /tmp/_hg_prev.txt) 条 / 本次失败 $(wc -l < /tmp/_hg_cur.txt) 条"
NEW=$(LC_ALL=C comm -13 /tmp/_hg_prev.txt /tmp/_hg_cur.txt)
if [ -n "$NEW" ]; then
  echo "闸门: **有新增失败** ❌ 拒绝推送:"; printf '%s\n' "$NEW"; exit 1
fi
echo "闸门: 无新增失败 ✅ —— 允许推送"
