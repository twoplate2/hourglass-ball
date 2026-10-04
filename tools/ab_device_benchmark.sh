#!/bin/bash
# 在 MuMu 模拟器上对两个 git 版本做**交替多轮**帧率 A/B。
#
# 用法:
#   tools/ab_device_benchmark.sh <revA> <revB> [轮数, 默认 5]
# 例:
#   tools/ab_device_benchmark.sh 7c8d094 94df8bc 5
#
# 产物: benchmark_logs/ab/<revA|revB>_r<n>__<时间戳>.txt
#       benchmark_logs/ab/_hashes/<tag>.txt   ← 该轮设备端代码的 sha256 指纹
# 分析: python tools/analyze_ab.py benchmark_logs/ab --arm-a <revA> --arm-b <revB>
#
# ⚠️ 八条硬约束(每条都踩过坑, 别改):
#  1. 每个臂必须推**自己那一套**文件 —— 至少 main.py / app_version.py / frame_benchmark.py
#     / tools/flow_texture_experiment.py。最后这个最容易漏: 两版差 96 行(v1.2 收 particles
#     字典表, v1.21 收 view+indices 并行数组), 漏推会
#     `AttributeError: 'DictFlowView' object has no attribute 'use_np'` → **应用秒退**。
#  2. **flow_numpy.py 的有无必须在宿主机判断**, 再以字面量(0/1)展开进 adb shell 的命令串。
#     写成 `if [ -f $d/tools/flow_numpy.py ]` 时 $d 是**宿主机**路径, 在设备上恒为假 →
#     v1.21 反被 `rm` 掉 flow_numpy.py; 而它的 main.py 把 `import numpy` 与 `import flow_numpy`
#     放在**同一个 try 块**里 ⇒ `_np` 被一起置 None ⇒ **同时丢掉 numpy 物理与 numpy 渲染打包**,
#     等于拿一个残废的 v1.21 去做对照 —— 会"证明" v1.2 更快。实测差距: 物理栏 1.4ms → 3.0ms。
#  3. **轮询/拉取必须用应用私有目录** `/data/data/$PKG/files/benchmark_logs`。
#     基准跑完的**自动保存**写的是 `user_data_dir/benchmark_logs`(两臂都一样);
#     而 `/sdcard/Android/data/$PKG/files/benchmark_logs` 只有结果页的「保存文件」按钮才写,
#     脚本从不点它 ⇒ 轮询外部目录会**每轮空等到超时**, 而日志其实已经躺在设备上了。
#  4. **必须 `export MSYS_NO_PATHCONV=1`**(Git Bash 会把 `/data/...` 转成 Windows 路径),
#     而开了它之后 **push 的源路径必须是 Windows 形式** —— 否则 `/tmp/...` 原样传给 adb.exe
#     解析不了, **push 全部静默失败**。故 WORK 由 `pwd -W` 取 Windows 形式。
#     (pull 的目的地用**相对路径**即可, 相对 cwd 解析, 不受此坑影响。)
#  5. **推送后必须逐文件比对 sha256**(设备端 vs 宿主机)。adb root 会中途掉, 掉后 push 静默
#     失败 → 应用还在跑旧文件, 于是"实验结论"全是假的(本项目被骗过一轮)。只比字节数不够:
#     /data/local/tmp 里残留的陈旧文件会让 cp 出"正确"结果, 恰好掩盖 push 失败。
#  6. **入口字节码必须一起删**: p4a 是「先 .pyc 后 .py」找入口, app 目录里可能有构建产出的
#     main.pyc / app_version.pyc / frame_benchmark.pyc。只清 `__pycache__/*.pyc` 时, 残留的
#     main.pyc 会让本臂的 main.py **根本不执行**, 两臂跑同一份旧代码而 sha256 校验毫无提示。
#  7. 存活检查**必须重试** —— 单次 adb 抖动会把还在跑的基准判死, 然后 force-stop 掐掉它;
#     越是瞬时故障(root 掉、push 失败)越要留在 ATTEMPTS 的重试预算里, 别直接 return。
#  8. 判定"应用死了"之前**先查有没有新日志落盘**(落盘即成功)。
#  9. **开跑前必须先确认没有别的进程在驱动同一台设备**: 上一轮会话遗留的 A/B 循环
#     (`ab_loop*.sh`) 会在后台同时推文件、force-stop、启动、滑动点击 —— 两个脚本抢一台
#     模拟器时, 你拉到的"本轮"日志很可能是**对方启动的那次运行**产生的, 臂也随时被换掉。
#     实测踩过: 遗留进程跑了近 50 分钟, 把我三轮全部打乱。开跑前 `ps -ef | grep ab_loop`。
#  10. 拉回来的日志要过**闸门**(verify_log): code_hash 必须等于本臂 main.py 的 sha256 前 12 位
#      (应用自己算的, 已实测吻合 —— 上一轮数据对不上是因为当时推的是**改过的** main.py)、
#      渲染器必须是 texture、三档齐全且平均帧率正常。不合格就丢弃重试, 不许混进中位数。
#
# ⚠️ 一轮基准约 **25–40 秒**(1s+5s+15s 全部跑完才落盘), 加上启动约 1 分钟。
#    轮询按 3s 一次、最多 6 分钟(正常情况下 15 倍余量)。
# ⚠️ 判据: 比 **1% low / p90**, **不比平均**(apk/README 记档: 平均帧率在本机非有效指标,
#    跑间 ±12%)。**中位差要大于组内极差才算可分辨。** 并用 physics_ms 核对"对照组是不是
#    真的那一版"(见约束 2)。
set -u
export MSYS_NO_PATHCONV=1

ADB="${ADB:-/c/Program Files/Netease/MuMu/nx_device/15.0/shell/adb.exe}"
PKG=org.shalou.hourglass
DEV_APP=/data/data/$PKG/files/app
DEV_TMP=/data/local/tmp
DEV_LOGS=/data/data/$PKG/files/benchmark_logs      # 约束 3: 自动保存写这里
# ⚠️ **app 的 uid 必须现查, 不能写死**(2026-10-04 实测踩到): 重装一次 app 就会换 uid
#   (u0_a66 → u0_a68), 而推送后 `chown` 写死旧 uid ⇒ 文件属主不是运行 app 的那个用户
#   ⇒ 应用读不到 main.py ⇒ logcat 里 `Entrypoint not found (.py), abort.` ⇒ 基准一轮都出不来。
#   症状还极具误导性: 脚本自己报"存活 ✓"(进程在), 但 app 的 Activity 起来 0.4 秒就被关掉,
#   表现成"基准启动, 轮询落盘"然后 6 分钟无产出。见 memory: hourglass-device-push-ownership。
APP_UID=$("$ADB" shell stat -c %u "$DEV_APP" 2>/dev/null | tr -d '\r')
case "$APP_UID" in
  ''|*[!0-9]*) echo "!! 取不到 $DEV_APP 的 uid(stat 返回 '$APP_UID') —— 先跑一次 app 让 Android 建目录"; exit 1;;
esac
APP_USER="u0_a$((APP_UID - 10000))"
echo "app uid = $APP_UID ($APP_USER, 现查)"
OUT=benchmark_logs/ab
ROOT="$(pwd -W 2>/dev/null || pwd)"                # 约束 4: Windows 形式, adb push 只认它
WORK="$ROOT/$OUT/_work"
POLL_ITERS="${POLL_ITERS:-120}"                    # × 3s = 6 分钟/轮
ATTEMPTS="${ATTEMPTS:-2}"

# 要推的文件(存在才推; 本臂不存在的文件在设备上必须被删掉)
FILES="main.py app_version.py frame_benchmark.py tools/flow_texture_experiment.py tools/flow_batch_experiment.py tools/flow_numpy.py"
# 约束 6: 入口/同名字节码, 必须删
PYCS="main.pyc app_version.pyc frame_benchmark.pyc tools/flow_texture_experiment.pyc tools/flow_batch_experiment.pyc tools/flow_numpy.pyc"

REV_A="${1:?用法: $0 <revA> <revB> [轮数]}"
REV_B="${2:?用法: $0 <revA> <revB> [轮数]}"
ROUNDS="${3:-5}"

# 护栏 1: 同一台设备上**只能有一个**驱动进程。踩过 —— 上一轮会话遗留的 ab_loop*.sh 在后台
# 同时推文件/force-stop/启动/滑动点击, 拉回来的"本轮"日志其实是对手启动的那次运行产生的,
# 臂也会在测量中途被换掉。它们不会自己退出, 上一轮会话结束了还在跑。
# ⚠️ 别用 pgrep: 本机 Git Bash **没有 pgrep**, 命令会静默失败(2>/dev/null 吞掉) ->
# 护栏恒假、脚本照常启动。改用 mkdir 原子锁, 跨平台且不依赖任何外部命令。
mkdir -p "$WORK" 2>/dev/null
LOCK="$WORK/.running"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "!! 另一轮 A/B 正在跑(锁: $LOCK)。"
  echo "   确认没有别的脚本在跑后, 删掉该目录再试:  rmdir $LOCK"
  exit 1
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
# 护栏 2: 产物目录不按"次"隔离, 重跑会把两代产物混进同一目录 → 两张表各取一半、静默不一致。
if ls "$OUT"/*.txt >/dev/null 2>&1; then
  echo "!! $OUT 里已有 $(ls "$OUT"/*.txt | wc -l) 个 .txt。先归档再跑, 例如:"
  echo "     mv $OUT/*.txt $OUT/_archive_$(date +%m%d_%H%M)/"; exit 1
fi

say() { echo "[$(date +%H:%M:%S)] $*"; }

proc_count() { "$ADB" shell 'ps -A 2>/dev/null | grep -c org.shalou.hourglass' 2>/dev/null | tr -d '\r' | head -1; }

alive() {   # 约束 7
  local i c
  for i in 1 2 3; do
    c=$(proc_count); [ -n "$c" ] && [ "$c" != "0" ] && return 0
    sleep 2
  done
  return 1
}

need_root() {   # 约束 5 的前置: 读写 /data/data 需要 root
  local u
  u=$("$ADB" shell id -u 2>/dev/null | tr -d '\r')
  [ "$u" = "0" ] && return 0
  say "  adb 非 root(uid=${u:-?}), 尝试 adb root"
  "$ADB" root >/dev/null 2>&1
  "$ADB" wait-for-device >/dev/null 2>&1
  sleep 2
  u=$("$ADB" shell id -u 2>/dev/null | tr -d '\r')
  [ "$u" = "0" ] || { say "  !! adb root 失败(uid=${u:-?})"; return 1; }
}

app_version_of() {  # $1=目录 —— 别用 grep -o '"[0-9.]*"': 文件开头的三引号文档串会先匹配到一对空引号
  sed -n 's/.*APP_VERSION *= *"\([^"]*\)".*/\1/p' "$1/app_version.py" | head -1
}

last_log() { "$ADB" shell "ls -t $DEV_LOGS/ 2>/dev/null | head -1" | tr -d '\r'; }

extract() {  # $1=rev -> $WORK/$1/
  local rev="$1" d="$WORK/$rev" f
  rm -rf "$d"; mkdir -p "$d/tools"
  git show "$rev:main.py" > "$d/main.py" || return 1
  git show "$rev:app_version.py" > "$d/app_version.py" || return 1
  git show "$rev:frame_benchmark.py" > "$d/frame_benchmark.py" || return 1
  for f in flow_texture_experiment flow_batch_experiment flow_numpy; do
    git show "$rev:tools/$f.py" > "$d/tools/$f.py" 2>/dev/null || rm -f "$d/tools/$f.py"
  done
  # 空文件 = git show 失败, 不能当有效臂推上去。⚠️ flow_numpy.py 不在必查之列:
  # v1.2(7c8d094) 本来就没有它, 把它算进来会把好臂误判成"导出失败"。
  for f in main.py app_version.py frame_benchmark.py \
           tools/flow_texture_experiment.py tools/flow_batch_experiment.py; do
    [ -s "$d/$f" ] || { say "  !! 导出 $rev 的 $f 为空(git show 失败)"; return 1; }
  done
  say "  导出 $rev = v$(app_version_of "$d")  tools=[$(ls "$d/tools" | tr '\n' ' ')]"
}

push_arm() {  # $1=rev —— 约束 2 + 约束 5 + 约束 6
  local rev="$1" d="$WORK/$rev" f base have_np=0 rc=0 expect actual
  for f in $FILES; do
    base=$(basename "$f")
    [ -s "$d/$f" ] || continue
    "$ADB" push "$d/$f" "$DEV_TMP/$base" >/dev/null 2>&1 || { say "  push 失败 $f"; return 1; }
  done
  # 约束 2: 在宿主机判定, 以 0/1 字面量进设备命令串
  [ -f "$d/tools/flow_numpy.py" ] && have_np=1
  "$ADB" shell "set -e
    cp $DEV_TMP/main.py            $DEV_APP/main.py
    cp $DEV_TMP/app_version.py     $DEV_APP/app_version.py
    cp $DEV_TMP/frame_benchmark.py $DEV_APP/frame_benchmark.py
    cp $DEV_TMP/flow_texture_experiment.py $DEV_APP/tools/flow_texture_experiment.py
    cp $DEV_TMP/flow_batch_experiment.py   $DEV_APP/tools/flow_batch_experiment.py
    if [ $have_np -eq 1 ]; then
      cp $DEV_TMP/flow_numpy.py $DEV_APP/tools/flow_numpy.py
      chown $APP_USER:$APP_USER $DEV_APP/tools/flow_numpy.py; chmod 600 $DEV_APP/tools/flow_numpy.py
    else
      rm -f $DEV_APP/tools/flow_numpy.py
    fi
    rm -f $(for f in $PYCS; do echo -n "$DEV_APP/$f "; done)
    chown $APP_USER:$APP_USER $DEV_APP/main.py $DEV_APP/app_version.py $DEV_APP/frame_benchmark.py \
        $DEV_APP/tools/flow_texture_experiment.py $DEV_APP/tools/flow_batch_experiment.py
    chmod 600 $DEV_APP/main.py $DEV_APP/app_version.py $DEV_APP/frame_benchmark.py \
        $DEV_APP/tools/flow_texture_experiment.py $DEV_APP/tools/flow_batch_experiment.py
    rm -f $DEV_APP/__pycache__/*.pyc $DEV_APP/tools/__pycache__/*.pyc" >/dev/null 2>&1 \
    || { say "  设备端 cp 失败"; return 1; }

  # 约束 5: 逐文件 sha256(不是字节数 —— 陈旧 /data/local/tmp 会掩盖 push 失败)
  for f in $FILES; do
    if [ -s "$d/$f" ]; then
      expect=$(sha256sum "$d/$f" | cut -d' ' -f1)
      actual=$("$ADB" shell "sha256sum $DEV_APP/$f 2>/dev/null" | tr -d '\r' | cut -d' ' -f1)
      [ "$expect" = "$actual" ] || { say "  !! 校验失败 $f: 期望 ${expect:0:12} 实得 ${actual:0:12}"; rc=1; }
    else
      actual=$("$ADB" shell "[ -e $DEV_APP/$f ] && echo LEFTOVER" | tr -d '\r')
      [ -z "$actual" ] || { say "  !! $f 本臂不该有, 设备上却残留"; rc=1; }
    fi
  done
  [ $rc -eq 0 ] || return 1
  # 约束 6 的回读: 确认跑的是本臂版本(p4a 优先 .pyc, 残留即绕过 .py)
  local devver
  devver=$("$ADB" shell "sed -n 's/.*APP_VERSION *= *\"\\([^\"]*\\)\".*/\\1/p' $DEV_APP/app_version.py" | tr -d '\r' | head -1)
  [ "$devver" = "$(app_version_of "$d")" ] || { say "  !! 设备端版本回读不符: 期望 $(app_version_of "$d") 实得 ${devver:-空}"; return 1; }
  say "  推送校验 ✓ v$devver flow_numpy=$have_np"
}

write_hashes() {  # $1=rev $2=tag —— 存到 _hashes/ 子目录, 避开 analyze_ab 的 *.txt 扫描(非递归)
  local rev="$1" tag="$2" d="$WORK/$1" f
  { echo "# $tag 设备端已校验的代码指纹 (sha256)"
    for f in $FILES; do
      [ -s "$d/$f" ] && printf "%s  %s\n" "$(sha256sum "$d/$f" | cut -d' ' -f1)" "$f"
    done
  } > "$OUT/_hashes/${tag}.txt"
}

dump_logcat() { "$ADB" logcat -d > "$OUT/logcat_$1.txt" 2>&1; say "  logcat -> $OUT/logcat_$1.txt"; }

summary_line() {  # $1=日志文件 —— 立刻看得见这一轮跑的是什么
  local t="$1"
  printf '      ver=%s renderer=%s hash=%s' \
    "$(grep -m1 -o 'app_version=[0-9.]*' "$t" | cut -d= -f2)" \
    "$(grep -m1 -o 'Flow renderer: [A-Za-z_]*' "$t" | cut -d' ' -f3)" \
    "$(grep -m1 -o 'code_hash=[0-9a-f]*' "$t" | cut -d= -f2)"
  for p in 1 5 15; do
    printf '  %ss[phys=%s low=%s avg=%s]' "$p" \
      "$(awk -v P="Period: ${p}s" '$0==P{f=1} f&&/Stage means:/{split($0,a,"physics_ms="); split(a[2],b,","); print b[1]; exit}' "$t")" \
      "$(awk -v P="^${p} 秒" '$0~P{f=1} f&&/1% low/{print $6; exit}' "$t")" \
      "$(awk -v P="^${p} 秒" '$0~P{f=1} f&&/1% low/{print $2; exit}' "$t")"
  done
  echo
}

verify_log() {  # $1=日志 $2=rev —— 拉回来先过闸门, 不合格的轮次不许进中位数
  local t="$1" d="$WORK/$2" expect chash rend miss=0 p a
  # 端到端代码身份: 应用自己算的 code_hash 必须等于本臂 main.py 的 sha256 前 12 位。
  # (frame_benchmark.py 对**正在跑的** main.py 取 sha256[:12]; 已实测吻合。)
  expect=$(sha256sum "$d/main.py" | cut -c1-12)
  chash=$(grep -m1 -o 'code_hash=[0-9a-f]*' "$t" | cut -d= -f2)
  if [ -z "$chash" ]; then
    say "  !! 日志无 code_hash, 无法核对代码身份 ⇒ 本轮作废(两臂都会写这一行, 没有兼容负担)"
    return 1
  elif [ "$chash" != "$expect" ]; then
    say "  !! 代码指纹不符: 日志 code_hash=$chash 本臂 main.py=$expect ⇒ 跑的不是本臂代码"
    return 1
  fi
  if grep -q '测试已取消' "$t"; then say "  !! 该轮基准被取消"; return 1; fi
  rend=$(grep -m1 -o 'Flow renderer: [A-Za-z_]*' "$t" | cut -d' ' -f3)
  [ "$rend" = "mesh_endpoint_texture" ] || { say "  !! 渲染器不是 texture 而是 '$rend' ⇒ 本轮不可比"; return 1; }
  for p in 1 5 15; do
    a=$(awk -v P="^${p} 秒" '$0~P{f=1} f&&/1% low/{print $2; exit}' "$t")
    if [ -z "$a" ]; then say "  !! 缺 ${p}s 段"; miss=1; continue; fi
    awk -v x="$a" 'BEGIN{exit !(x+0 >= 30)}' || { say "  !! ${p}s 段平均只有 ${a} FPS, 该段没正常跑"; miss=1; }
  done
  [ $miss -eq 0 ] || return 1
  return 0
}

run_once() {  # $1=rev $2=round
  local rev="$1" round="$2" tag="$1_r$2" prev now i attempt devver
  for attempt in $(seq 1 "$ATTEMPTS"); do
    say "=== $tag (第 $attempt/$ATTEMPTS 次尝试) ==="
    need_root || { say "  !! 提权失败, 重试"; continue; }            # 约束 7
    extract "$rev" || return 1                                      # rev 错/文件缺失不可自愈
    push_arm "$rev" || { say "  !! 推送/校验失败, 重试"; continue; }  # 约束 7
    write_hashes "$rev" "$tag"
    "$ADB" shell am force-stop $PKG >/dev/null 2>&1; sleep 2
    "$ADB" shell monkey -p $PKG -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
    sleep 16
    alive || { say "  !! 启动后秒退"; dump_logcat "$tag"; continue; }
    say "  存活 ✓"
    prev=$(last_log)
    # ⚠️ 长按现在开的是**隐藏菜单**(1.52 起长按版本号那块 = 菜单入口), 不再是直接开基准。
    # 所以要多一步: 长按 → 点菜单里的「性能测试」→ 再点基准弹窗的「开始测试」。
    # 坐标是设备真实像素(1080x2400, density 280), 由 `screencap` 截图量的;
    # 改弹窗布局/按钮顺序**必须重新量**, 否则基准根本不会启动(A/B 会静默跑成 0 轮)。
    "$ADB" shell input swipe 546 2328 547 2328 3500   # 长按 3.5s 触发菜单(>3s 阈值, 位移 1px < dp(12))
    sleep 3
    "$ADB" shell input tap 536 1392                   # 菜单里的「性能测试」
    sleep 2
    "$ADB" shell input tap 220 1426                   # 基准弹窗的「开始测试」
    say "  基准启动, 轮询落盘"
    now="$prev"
    for i in $(seq 1 "$POLL_ITERS"); do
      now=$(last_log)
      [ -n "$now" ] && [ "$now" != "$prev" ] && break     # 约束 8: 落盘优先于判死
      if [ $((i % 40)) -eq 0 ] && ! alive; then
        say "  !! 基准途中应用消失(~$((i*3))s)"; dump_logcat "$tag"; break
      fi
      sleep 3
    done
    if [ -z "$now" ] || [ "$now" = "$prev" ]; then
      say "  !! 本轮未产出(6 分钟无新日志)"; continue
    fi
    if ! "$ADB" pull "$DEV_LOGS/$now" "$OUT/${tag}__${now}" >/dev/null 2>&1; then
      say "  !! pull 失败 $DEV_LOGS/$now"; dump_logcat "$tag"; continue
    fi
    if [ ! -s "$OUT/${tag}__${now}" ]; then
      say "  !! 拉下来是空文件, 不计入本轮"; rm -f "$OUT/${tag}__${now}"; continue
    fi
    if ! verify_log "$OUT/${tag}__${now}" "$rev"; then
      say "  !! 本轮未过闸门, 丢弃重试"; rm -f "$OUT/${tag}__${now}"; continue
    fi
    say "  => $OUT/${tag}__${now} ($(wc -c < "$OUT/${tag}__${now}") bytes)"
    summary_line "$OUT/${tag}__${now}"
    return 0
  done
  return 1
}

mkdir -p "$OUT/_hashes" "$WORK"
say "A/B: $REV_A vs $REV_B, $ROUNDS 轮交替; 工作目录 $WORK"
for r in $(seq 1 "$ROUNDS"); do
  for rev in "$REV_A" "$REV_B"; do
    run_once "$rev" "$r" || say "!!! $rev r$r 未产出"
  done
done
say "=========== 全部完成 ==========="
ls -l "$OUT"
echo
echo "下一步: python tools/analyze_ab.py $OUT --arm-a $REV_A --arm-b $REV_B"
