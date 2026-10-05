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
# 用法:
#   bash tools/_watch_build.sh --baseline      # **推送前**记下当前最新 run 号
#   bash tools/_watch_build.sh <基线run号> [最长分钟数]   # **推送后**等 > 基线 的那条出结论
#
# ⚠️ **必须给基线**。第一版只看"列表里最新一条已完成的", 于是刚推完就抓到
#    **上一次 push 的结果**并退出 —— 实测它报了个"run #188 成功", 而那是 1.115,
#    不是刚推的 1.116。**看着有结论, 其实答错了题**, 比不报还坏。
#    (HTML 里没有 sha, 所以用 run 号单调递增来判断"这条是不是我这次的"。)
set -u
REPO=twoplate2/hourglass-ball
MAXMIN=45
if [ "${1:-}" = "--baseline" ]; then
  curl -fsS -A "Mozilla/5.0" "https://github.com/$REPO/actions" 2>/dev/null \
    | PYTHONIOENCODING=utf-8 python -c '
import io, re, sys
h = sys.stdin.read()
nums = [int(m.group(1)) for m in re.finditer(r"Run (\d+) of Build APK", h)]
print(max(nums) if nums else 0)
'
  exit 0
fi
BASE="${1:-0}"
TARGET=$((BASE + 1))     # 只看这一个号: 它就是"我这一次"
[ $# -ge 2 ] && MAXMIN="$2"
INTERVAL="${HG_WATCH_INTERVAL:-300}"     # 默认 5 分钟一轮(用户定的)
SHA="$(git rev-parse HEAD)"
SHORT="${SHA:0:7}"

api() { curl -fsS -A "Mozilla/5.0" "https://github.com/$REPO/$1" 2>/dev/null; }

# ⚠️ **不要走 api.github.com**: 未认证 60 次/小时, 实测这个 IP 直接 403
#    "API rate limit exceeded" —— 脚本会静默空转(第一次就是这么哑的)。
#    改抓 **actions 列表页的 HTML**: 每条的 aria-label 就写着结论, 且走 github.com 域。
#       aria-label="completed successfully: Run 188 of Build APK. 1.115 ..."
parse() {
  # ⚠️ **只认 `目标号` 那一条**, 不是"比基线大的最新一条"。
  #    第二版就是后者 —— 5 分钟轮询窗口里如果已经跑完了两次构建, 它会取**最新**那条,
  #    把中间那条(可能是**红的**)整个跳过去。实测: 1.118 的监视报出了 1.119 的成功。
  #    「比基线大的最新一条」不等于「我这一次的那条」。
  PYTHONIOENCODING=utf-8 python -c '
import io, re, sys
h = io.open(sys.argv[1], encoding="utf-8", errors="replace").read()
want = sys.argv[2]
for m in re.finditer(r"aria-label=\"([^\"]*?Run (\d+) of ([^\"]*?))\"", h):
    label, num, title = m.group(1), m.group(2), m.group(3)
    if "Build APK" not in title or num != want:
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
' "$1" "$TARGET"
}

deadline=$(( $(date +%s) + MAXMIN * 60 ))
tries=0
# ⚠️ 临时文件要落在**已被 gitignore 的目录**里 —— 落到 `.` 会把仓库根目录搞脏
#    (实测留了个 `_ci_watch.html`, 刚好赶上"绝不再乱加文件"这条)。
TMP="benchmark_logs/_ci_watch.html"
while :; do
  tries=$((tries + 1))
  if api "actions" > "$TMP" 2>/dev/null && [ -s "$TMP" ]; then
    IFS=$'\t' read -r status concl num title <<EOF
$(parse "$TMP")
EOF
    if [ "$status" = "completed" ]; then
      if [ "$concl" = "success" ]; then
        echo "CI ✅ $SHORT (run #$num) 构建成功: $title"
      else
        echo "CI ❌ $SHORT (run #$num) 构建 **$concl**: $title —— https://github.com/$REPO/actions/runs/$num"
      fi
      exit 0
    fi
    # 还在跑 / 排队 / run 号还没超过基线: 不打字(避免刷屏), 继续等
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "CI ⏳ run #$TARGET 等了 ${MAXMIN} 分钟仍未出结论(轮询 $tries 次) —— 需要人工看一眼"
    exit 1
  fi
  sleep "$INTERVAL"
done
