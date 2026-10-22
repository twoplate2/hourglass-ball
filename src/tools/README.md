# 工具索引

这里仅放构建、测试、诊断与素材加工工具，不进入 APK。
应用运行模块在 `src/app/`，不能当作临时脚本清理。
每个文件的用途与限制见 [逐文件索引](../../docs/file-index.md)。

## 构建与维护

- `android_hook.py`：Android Manifest 构建钩子。
- `check_release_version.py` / `test_release_version.py`：版本与提交规则。
- `check_material_release.py` / `test_material_release.py`：核对本次材质发布的实图、源码身份和完整 PC 对照；拒绝陈旧或不完整证据，旧回退身份检查保留作历史工具。
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
`--profile full` 仅在用户另行要求完整回归时使用。当前仅在隐藏 PC 窗口验证；
规则与缺口见 [改完以后怎么检查](../../design/checks/README.md)。
`check_design_pages.py` 核对全部 HTML 图片和链接，已接入每日基础检查。

- `verify_*.py` / `test_*.py`：逻辑回归与标量、向量化等价检查。
- `test_stream_rendering.py`：调用真实共用公式检查密度、落点补齐、收尾和绘制分支等价；无窗口，可放到模拟器的 Python 环境执行。
- `test_ui_polish.py`：无窗口执行真实界面方法，核对计时状态文案、暂停时设置预览及退出恢复、沙纹浓淡滑杆映射；不验证游标外观或设备交互。
- `test_grain_style.py`：无窗口检查材质时间相位、局部落点范围和连续边界，并执行真实 uniform 更新方法，防止回绕后使用旧时间。
- `material_visual_probe.py`：历史设备材质专项取图入口，当前 PC 规则下不执行；其中状态、几何与绘制摘要函数可供隐藏 PC 取图复用。固定步进图片不能当成实时或性能测试。
- `inspect_flow.py` / `_pixdiff.py` / `_side_by_side.py`：取图与视觉对照。
- `profile_frames.py` / `_hot_desktop.py`：桌面耗时分析。
- `_render_golden.py` / `_splash_golden.py`：版本化指纹参照；不可随意更新基线。
- `generate_*.py` / `make_clock_loop.py`：加工音频素材。
- 其余保留的探针用于单项诊断；仅保留不代表这轮已验证它们有效。

旧实验脚本移到 `_temp/retired-tools/`，清单在该目录的 `inventory.json`。
“移出”只表示它们不在当前保留工具及其引用链中，不等于已经证明永远无用。
历史脚本常依赖原来的 `tools/` 位置，需要恢复原环境后再检查，不能直接当作可运行工具。

所有测试输出写 `_temp/`。当前只在真正隐藏的 PC 窗口和独立设置目录运行、验证；
不连接或操作 MuMu、ADB 或真机。设备脚本仅作历史保留，不按旧授权自动执行。

## 速度与形状外观检查

当前用 `capture_stream_motion.py` 采集实际隐藏画布连续帧，`check_stream_motion_shape.py`
独立读取PNG位移和轮廓；`--sample-hz`为抽样起点频率，`--pair-hz`为配对时间间隔的倒数，
默认10/20，正式同时检查30/30与60/60。这是图像运动检查，不是实时帧率结果。
`check_motion_shape_release.py`绑定当前3.6证据，并要求3.5反例同时失败慢速与矩形两项。
`check_stream_wrap.py`检查实际粒子循环和轮廓循环边界；完整过程仍由 `record_short_grain.py`录制。

## 历史3.5短颗粒外观检查

当前发布入口是 `check_short_grain_release.py`。原 `check_material_release.py` 保留核对
3.4 的颜色修改历史，不适用于本次短颗粒几何和飞溅变化。
`verify_short_grain_runtime.py` 只在真正隐藏的 Windows PC 窗口采集，并隔离设置目录；
`--renderer texture` 使用新的默认短颗粒和批量飞溅，`native` 使用 PC 默认路径，
`legacy` 用于旧源码，`fallback` 强制验证 Line 回退。`--video` 录制 50 秒计时及
3 秒暂停、后续落定；帧时间按墙钟保留，录制开销不用于声称实时帧率通过。
`check_short_grain_visual.py` 的像素检查必须结合人工审阅，不等同于美观评分。
