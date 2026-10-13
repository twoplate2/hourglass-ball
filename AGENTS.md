# 工作规则

- 只在 PC 上运行和验证，不连接或操作真机、MuMu、ADB。
- 自动取图使用真正隐藏的 PC 窗口及独立设置目录，不干扰桌面工作。
- 取消或中断的测试不能记为通过。
- 沙粒密度、玻璃轮廓和肉眼可见细节不能为帧率让步；优先优化 1% low 和最慢帧。
- 每次 push 前递增 `src/app/app_version.py`：版本号同段数时最后一段 +1(`1.0` -> `1.1`)；
  里程碑式的跃迁(段数或前几段变化，如 `0.1.3` -> `1.0`)也允许，但必须严格递增。
- APK 版本、Benchmark 标题、复制报告使用同一个 `APP_VERSION`，不要分别写死。
- 提交标题以新版本号开头，其余标题和提交说明用中文，例如 `0.1.1 修复沙流衔接`。
- 不把未验证的合批实验默认启用，不将取消或未完成的测试记为通过。
- push 前运行版本检查；本仓库使用 `.githooks/pre-push`，CI 也检查版本和提交标题。
- 改动前先看 `design/checks/rules/how-to-check.md` 和图文用例 `design/checks/rules/sand-flow.html`，用 `python src/tools/run_regression.py --plan` 列出关联检查。
- 改 A 后也要检查关联 B；未知影响按完整回归处理。新增故障先补失败用例，再修应用。
- 外观改动只回归本次外观及相邻连接，不自动扩展到帧率、音频或完整应用回归；这些检查由用户另行安排。
- 此次 Pymunk 接入使用 `verify_pymunk_integration.py` 和 `check_pymunk_release.py` 核对 PC 运行、外观与源码身份；不据此声称 Android、音频或性能验收通过，不重存旧基线掩盖失败。
