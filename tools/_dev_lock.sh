#!/bin/bash
# 设备**独占锁** —— 多个进程/评审 agent 同时驱动同一台 MuMu 会把测量全打乱。
#
# 项目踩过(`CLAUDE.md` 三条流程红线附近): 「开跑前先 `ps -ef | grep ab_loop` 确认没有
# 别的进程在驱动同一台设备 —— 踩过: 上一轮遗留的三个后台循环把测量全打乱了」。
# 那一条靠**人眼**看, agent 并行时不好使 ⇒ 这里用 `mkdir` 的原子性做一把粗锁。
#
# 用法:
#     bash tools/_dev_lock.sh acquire || exit 1     # 拿锁(默认最多等 10 分钟)
#     ...驱动设备...
#     bash tools/_dev_lock.sh release
#
# 也可以套着用(自动释放):
#     bash tools/_dev_lock.sh run tools/_one_bench.sh mytag
#
# ⚠️ 锁**有老化**: 目录超过 `STALE_MIN` 分钟就当成"上一个进程崩了"直接抢过来,
#    否则一次异常退出会让后面所有人卡满超时。
set -u
LOCK="${HG_DEV_LOCK:-/tmp/hg_dev.lock}"
STALE_MIN="${HG_DEV_LOCK_STALE_MIN:-25}"
WAIT_MIN="${HG_DEV_LOCK_WAIT_MIN:-10}"

stale() {
  [ -d "$LOCK" ] || return 1
  local age=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
  [ "$age" -gt $(( STALE_MIN * 60 )) ]
}

case "${1:-acquire}" in
  acquire)
    end=$(( $(date +%s) + WAIT_MIN * 60 ))
    while :; do
      if mkdir "$LOCK" 2>/dev/null; then
        echo "$$" > "$LOCK/pid" 2>/dev/null || true
        echo "dev-lock acquired ($LOCK, pid $$)"
        exit 0
      fi
      if stale; then
        echo "dev-lock: 发现陈旧锁(>${STALE_MIN}min), 抢占"
        rm -rf "$LOCK"
        continue
      fi
      if [ "$(date +%s)" -ge "$end" ]; then
        echo "dev-lock: 等锁超过 ${WAIT_MIN} 分钟, 放弃 —— 谁占着: $(cat "$LOCK/pid" 2>/dev/null)"
        exit 1
      fi
      sleep 5
    done
    ;;
  release)
    rm -rf "$LOCK"
    echo "dev-lock released"
    ;;
  run)
    shift
    bash "$0" acquire || exit 1
    trap 'bash "$0" release >/dev/null' EXIT
    "$@"
    ;;
  *)
    echo "用法: $0 acquire|release|run <命令...>" >&2
    exit 2
    ;;
esac
