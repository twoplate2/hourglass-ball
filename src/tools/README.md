# 工具索引

这里仅放构建、测试、诊断与素材加工工具，不进入 APK。
应用运行模块在 `src/app/`，不能当作临时脚本清理。
每个文件的用途与限制见 [逐文件索引](../../docs/file-index.md)。

## 构建与维护

- `android_hook.py`：Android Manifest 构建钩子。
- `check_release_version.py` / `test_release_version.py`：版本与提交规则。
- `_prepush.sh` / `_watch_build.sh`：发布检查与 CI 监视。
- `test_repository_layout.py`：资源位置、文档链接、暂存区隔离检查。
- `_pack_source_md.py`：按运行文件白名单生成代码资料包。
- `build_sand_flow_docs.py`：生成图文规则、逐编号版本结果和索引；`--new-record 版本号` 创建全未测记录，不启动应用或执行测试。
- `capture_sand_flow_media.py`：只在 `127.0.0.1:16416` MuMu 采集指定版本的阶段截图与完整录像，先核对 APK 和运行代码；源码覆盖模式必须提供部署摘要，并与 APK 安装版分开标记。
- `deploy_mumu_source.py`：备份预留 MuMu 后覆盖当前应用源码和资源、回读摘要；不会生成或安装新 APK。
- `prepare_sand_flow_media.py`：裁出原速循环片段、整理逐编号图片和素材记录，保留完整录像；离线处理媒体，不执行应用测试。
- `_temp.py`：暂存区列表与清理预览。

## PC 验证

统一入口：`python src/tools/run_regression.py --plan` 列出关联项，
外观改动只回归本次外观及相邻连接，CI 执行 `--profile core` 基础检查；
`--profile full` 仅在用户另行要求完整回归时使用。本次推送核对 MuMu 外观证据；
规则与缺口见 [改完以后怎么检查](../../design/checks/README.md)。
`check_design_pages.py` 核对全部 HTML 图片和链接，已接入每日基础检查。

- `verify_*.py` / `test_*.py`：逻辑回归与标量、向量化等价检查。
- `test_stream_rendering.py`：调用真实共用公式检查密度、落点补齐、收尾和绘制分支等价；无窗口，可放到模拟器的 Python 环境执行。
- `inspect_flow.py` / `_pixdiff.py` / `_side_by_side.py`：取图与视觉对照。
- `profile_frames.py` / `_hot_desktop.py`：桌面耗时分析。
- `_render_golden.py` / `_splash_golden.py`：版本化指纹参照；不可随意更新基线。
- `generate_*.py` / `make_clock_loop.py`：加工音频素材。
- 其余保留的探针用于单项诊断；仅保留不代表这轮已验证它们有效。

旧实验脚本移到 `_temp/retired-tools/`，清单在该目录的 `inventory.json`。
“移出”只表示它们不在当前保留工具及其引用链中，不等于已经证明永远无用。
历史脚本常依赖原来的 `tools/` 位置，需要恢复原环境后再检查，不能直接当作可运行工具。

所有测试输出写 `_temp/`。用户已授权使用预留的 root MuMu；历史设备脚本须先适配当前
路径并核对实例，不能直接照跑。不在用户本机启动 PC 测试，不连接真机。
