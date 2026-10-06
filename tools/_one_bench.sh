#!/bin/bash
# 单臂设备基准: 把**工作区当前**的 main.py 推上 MuMu, 跑一次完整基准, 拉回日志并打印四栏。
#
# 为什么另起一个(不去用 `ab_device_benchmark.sh`): 那个按 **git rev** 取代码, 而迭代中要量的
# 常常是**未提交的工作区**(量完才决定要不要留)。这里走 `_review_push.sh`, 推的就是工作区。
#
# 用法: tools/_one_bench.sh [标签]
#   ⚠️ 一次约 1.5~2 分钟(开机 16s + 长按/点菜单 + 三轮 1/5/15 秒 + 收尾)。
#   ⚠️ **同一次运行里三档连着跑**, 所以三档的"当前状态"是互不相同的 —— 跨臂比要**同档比同档**。
set -eu
export MSYS_NO_PATHCONV=1
ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
SER="${SER:-127.0.0.1:16384}"
PKG=org.shalou.hourglass
DEV_LOGS=/data/data/$PKG/files/benchmark_logs
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${1:-one}"
# ⚠️ 给 adb 的**本地**路径必须是 Windows 形式: 脚本头 `MSYS_NO_PATHCONV=1` 关掉了自动转换,
#    用 `/e/...` 形式时 adb(Windows 程序)解析不了 ⇒ pull 静默失败(`_review_push.sh` 同处理)。
OUT_W="$(cd "$ROOT" && mkdir -p benchmark_logs/dev && cd benchmark_logs/dev && pwd -W)"
OUT="$ROOT/benchmark_logs/dev"
mkdir -p "$OUT"

say() { echo "[$TAG] $*"; }

# ① 推工作区(自带 sha256 校验 + 重启 + 存活检查)
bash "$ROOT/tools/_review_push.sh" >/tmp/_one_push.log 2>&1 || { say "!! 推送失败"; tail -20 /tmp/_one_push.log; exit 1; }
grep -q "存活" /tmp/_one_push.log || { say "!! 启动后没存活"; tail -20 /tmp/_one_push.log; exit 1; }
say "推送并重启 OK"

# ② 驱动: 长按版本区 3.5s → 菜单「性能测试」→ 基准弹窗「开始测试」
#    ⚠️ 坐标是**设备真实像素**(1080x2400, density 280)由 screencap 量的;
#    改弹窗布局/按钮顺序**必须重量**, 否则基准根本不会启动(会静默等到超时)。
prev=$("$ADB" -s "$SER" shell "ls -t $DEV_LOGS/ 2>/dev/null | head -1" | tr -d '\r')
"$ADB" -s "$SER" shell input swipe 546 2328 547 2328 3500
sleep 3
"$ADB" -s "$SER" shell input tap 540 2076
sleep 2
"$ADB" -s "$SER" shell input tap 255 1524
say "基准启动, 等落盘"

# ③ 轮询落盘(优先于判死)
now=""
for i in $(seq 1 120); do
  now=$("$ADB" -s "$SER" shell "ls -t $DEV_LOGS/ 2>/dev/null | head -1" | tr -d '\r')
  if [ -n "$now" ] && [ "$now" != "$prev" ]; then break; fi
  if [ $((i % 30)) -eq 0 ]; then
    c=$("$ADB" -s "$SER" shell 'ps -A 2>/dev/null | grep -c org.shalou.hourglass' | tr -d '\r')
    [ "$c" = "0" ] && { say "!! 基准途中应用消失(~$((i*3))s)"; exit 1; }
  fi
  sleep 3
done
[ -n "$now" ] && [ "$now" != "$prev" ] || { say "!! 6 分钟无新日志"; exit 1; }

# ④ 拉回 + 身份校验(code_hash 必须等于本地 main.py 的 sha256 前 12 位)
dst="$OUT_W/${TAG}__${now}"
"$ADB" -s "$SER" pull "$DEV_LOGS/$now" "$dst" >/dev/null 2>&1 || { say "!! pull 失败"; exit 1; }
[ -s "$dst" ] || { say "!! 拉下来是空文件"; exit 1; }
exp=$(sha256sum "$ROOT/main.py" | cut -c1-12)
got=$(grep -m1 -o 'code_hash=[0-9a-f]*' "$dst" | cut -d= -f2)
if [ "$exp" != "$got" ]; then
  say "!! code_hash 不符: 期望 $exp 实得 $got ⇒ 设备跑的不是本地这份, 本轮作废"
  exit 1
fi
say "code_hash $got ✓  -> $dst"
echo "----------------------------------------------------------------"
grep -E "^[0-9]+ 秒|^平均|^诊断耗时|^存活粒子|^Flow renderer|^环境" "$dst" | sed 's/^/  /'
echo "----------------------------------------------------------------"
