# 目录说明（每个文件夹是干什么的、要不要留）

> 2026-10-13 立。起因：用户问「那个沙漏文件夹怎么那么大？删掉任意一个都无法 build 成功吗？」
> —— 不是。**CI 构建只认 git 里跟踪的那 19 MB**；本地这 2.5 GB 里绝大部分是取证图/日志，
> 全新 clone 里根本不存在。本轮按「删掉就不能构建/就不能跑」把本地目录清到 **162 MB**。
> 判据来自一轮对抗审计（16 个 agent，读 buildozer 1.5.0 源码 + 两个独立透镜证伪）。

## 一、构建必需（删了 APK 就构建不出来、或构建出来的装不上/是坏的）

| 路径 | 是什么 | 谁在读 | 删了的后果 |
|---|---|---|---|
| `main.py` | APK 的应用入口 | buildozer 打包 + CI 闸门③ pyflakes | 构建出无入口的包 |
| `frame_benchmark.py` | 帧率基准（隐藏 benchmark） | `main.py:214` **顶层硬 import** | 装机启动即 ImportError |
| `app_version.py` | 版本号唯一真值 | 闸门①逐 commit `git show`、闸门②断言、buildozer `version.filename` | 构建直接失败 |
| `buildozer.spec` | 构建唯一配置 | buildozer + CI 缓存 key | 构建失败 |
| `.github/workflows/build-apk.yml` | CI 流水线本体（三道早退闸门 + 构建） | GitHub Actions | 没有流水线 |
| `tools/` | ①CI 闸门③对 `tools/*.py`（现 128 个）逐个 pyflakes ②其中 6 个是**运行时**经 `sys.path` 装载的模块 | CI + `main.py` | 有 undefined name ⇒ 构建红；运行时模块缺 ⇒ 静默退回慢路径 |
| `p4a/hook.py` | `buildozer.spec:26` 的 `p4a.hook` | buildozer→p4a | 横屏/可缩放注入静默丢失 |
| `fonts/NotoSansSC-Medium.otf` | 全局字体（`name="Roboto"` 覆盖） | `main.py:221` | APK 里中文全是豆腐块（桌面预览查不出来） |
| `sand_loop.wav` · `sounds/*.wav` · `sounds/voice/*.wav` | 沙沙声 + 水流/风/钟表 + 90 个播报词块 | `main.py` 音效与完成播报 | 对应功能静默 |
| `ui/startup_blank.png` | `presplash.filename` 指向它 | buildozer | 启动图退回框架默认 |
| `ui/slider_track.png` · `ui/slider_cursor.png` | 周期弹窗滑杆贴图 | `main.py:9757` | 退回 Kivy 默认浅灰滑杆 |
| `icon.png` | `icon.filename` | buildozer `--icon` | 应用图标不对 |

> 运行时 6 个模块：`flow_numpy.py` / `sand_flow_material.py` / `flow_texture_experiment.py` /
> `flow_batch_experiment.py` / `flow_splash_experiment.py` / `marker_batch_experiment.py`。

## 二、本地非构建必需（CI 一个字节都不读，但删了会影响**你自己的**工作流）

| 路径 | 是什么 | 为什么还留着 | 删了会怎样 |
|---|---|---|---|
| `benchmark_logs/` | 原本 2.1 GB 的取证图/日志，**现已清到只剩 1.3 MB**（参照臂 `flow_visual_sieveoff_default`） | `tools/_prepush.sh` 第 2/3 步要往这里**写**烟测渲染、并**读**参照臂；`tools/_watch_build.sh`（push 后盯 CI）把轮询文件写在这里 | 整个目录删掉 ⇒ 那两个脚本失效（预推送闸门变红 = 不许 push；CI 监视永远得不出结论） |
| `design/` | 给你看的 HTML 取证页（含 `design/img/`），9.9 MB | 你审阅视觉改动时看的就是它 | 无（页面重做即可） |
| `mp3/` | 音效原始素材（`zhongbiao.mp3` 等），284 KB | 删了就没法重新加工出 `sounds/*.wav` | 无（但不可再生） |
| `_kivyhome/` | 某个桌面探针的 `KIVY_HOME`，90 KB | `tools/_probe_neck_batch_equiv.py` 当环境用；**不能挪** | 那个探针跑不了（里面 `icon/*.png` 还会被打进 APK） |

## 三、仓库本体与文档

| 路径 | 说明 |
|---|---|
| `.git/`（135 MB） | 仓库历史，**push 必需**。这是本文件夹里除了代码外最大的一块，属必要成本 |
| `.gitignore` · `.gitattributes` · `.githooks/` | 忽略规则 / 行尾规则 / 本地 pre-push 钩子 |
| 19 个 `*.md` | 知识库（`CLAUDE.md` 是唯一的工作约定源、`QA_RULES.md` 是证据纪律、`README.md` 是经验教训）。CI 不读，但删了等于把积累的坑全忘掉 |

## 四、本轮删掉的（2.26 GB / 5370 个文件）

| 删的 | 原大小 | 是什么 | 还能找回来吗 |
|---|---|---|---|
| `benchmark_logs/`（除参照臂） | 2.1 GB | `_dl` / `_vid` / `_dev` / `_refs` / `_rec` / `ab` / 一堆 `flow_visual_*` 渲染输出 + 一个 38 MB 的旧 APK | **旧树 `E:\AI_Tools\other\shalou_claude\pc\apk` 里有一份完整拷贝** |
| `_temp/` | 180 MB | 待删暂存区（按定义就是"过几天就能删的"） | 同上（其中 9 个是我改过路径的副本，原版在旧树） |
| `_shot/` `_review/` | 196 MB | 历史取证图 / 面板转写与截图 | 同上 |
| `__pycache__/` ×2 | 0.8 MB | Python 字节码缓存 | 自动重建 |

> 另有 10 个**只在新家存在**的文件随 `_temp/` 一起删了（9 个是我把旧路径改掉的脚本副本 +
> 1 张 `tail60_side.png`）—— 原版都在旧树，改的那几行是绝对路径替换。
