#!/bin/bash
# 盯一次 push 的 GitHub Actions 结果。**push 完必须挂这个**, 不要等人去看。
#
# 用户 2026-10-06 定: 「你刚 push 之后再等 5 分钟, 5 分钟之后再去看看上一个 push
# 有没有成功、有没有失败; 如果还没成功再等 5 分钟; 发现有错误就通告主线程。不要让我来搞。」
#
# 为什么要有它: 1.111~1.114 **四次构建全失败**, 是用户自己起床后发现的。
# 本地闸门(verify_hourglass)只验**桌面**逻辑, **它绿不代表 APK 能构建出来** ——
# 这两条是完全独立的。所以「闸门绿了」不能当作"可以推送"的全部依据。
#
# 用法: tools/_watch_build.sh [sha] [最长分钟数]
#   输出协议(给 Monitor 用): **只在有结论时打一行**, 然后退出。
set -u
REPO=twoplate2/hourglass-ball
SHA="${1:-$(git rev-parse HEAD)}"
MAXMIN="${2:-45}"
INTERVAL="${HG_WATCH_INTERVAL:-300}"     # 默认 5 分钟一轮(用户定的)
SHORT="${SHA:0:7}"

api() { curl -fsS -A "Mozilla/5.0" "https://github.com/$REPO/$1" 2>/dev/null; }

# ⚠️ **不要走 api.github.com**: 未认证 60 次/小时, 实测这个 IP 直接 403
#    "API rate limit exceeded" —— 脚本会静默空转(第一次就是这么哑的)。
#    改抓 **actions 列表页的 HTML**: 每条的 aria-label 就写着结论, 且走 github.com 域。
#       aria-label="completed successfully: Run 188 of Build APK. 1.115 ..."
parse() {
  PYTHONIOENCODING=utf-8 python -c '
import io, re, sys
h = io.open(sys.argv[1], encoding="utf-8", errors="replace").read()
# 只看 Build APK 的条目, 取**最新那条**(列表是倒序的)
for m in re.finditer(r"aria-label=\"([^\"]*?Run (\d+) of ([^\"]*?))\"", h):
    label, num, title = m.group(1), m.group(2), m.group(3)
    if "Build APK" not in title:
        continue
    if "completed successfully" in label:
        st, cc = "completed", "success"
    elif "completed with failure" in label or "failed" in label:
        st, cc = "completed", "failure"
    elif "cancelled" in label:
        st, cc = "completed", "cancelled"
    elif "in progress" in label:
        st, cc = "in_progress", "-"
    else:
        st, cc = "queued", "-"
    print("%s\t%s\t%s\t%s" % (st, cc, num, re.sub(r"\s+", " ", title)[:70]))
    break
else:
    print("\t\t\t")
' "$1"
}

deadline=$(( $(date +%s) + MAXMIN * 60 ))
tries=0
TMP="${TMPDIR:-.}/_ci_watch.html"
while :; do
  tries=$((tries + 1))
  if api "actions" > "$TMP" 2>/dev/null && [ -s "$TMP" ]; then
    IFS=$'\t' read -r status concl num title <<EOF
$(parse "$TMP")
EOF
    if [ "$status" = "completed" ]; then
      if [ "$concl" = "success" ]; then
        echo "CI ✅ (run #$num) 构建成功: $title"
      else
        echo "CI ❌ (run #$num) 构建 **$concl**: $title —— https://github.com/$REPO/actions/runs/$num"
      fi
      exit 0
    fi
    # 还在跑/排队: 不打字(避免刷屏), 继续等
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "CI ⏳ 等了 ${MAXMIN} 分钟仍未出结论(轮询 $tries 次) —— 需要人工看一眼"
    exit 1
  fi
  sleep "$INTERVAL"
done
