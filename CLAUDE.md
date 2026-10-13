# 工作入口

先读 [AGENTS.md](AGENTS.md) 和 [改完以后怎么检查](design/checks/README.md)、[QA 纪律](design/checks/notes/qa-rules.md)。
根目录只保留当前规则；历次调试记录在 [决策历史](docs/archive/CLAUDE-history.md)，不是当前操作说明。

## 不变约束

- 不在用户本机桌面启动 PC 测试；运行验证使用用户预留的 root MuMu，通过指定实例 ADB 后台操作，不连接真机或控制桌面鼠标。历史命令先核对路径和实例，不能直接照跑。
- 沙粒密度、玻璃轮廓、肉眼可见细节不能为性能让步，优先关注 1% low 和最慢帧。
- 不凭直觉修改物理或核心参数；需要历史 PC v4 基准时，它位于 `E:\AI_Tools\other\shalou_claude\pc\hourglass_v4.py`，不在本仓库。
- 沙体和玻璃保持一致的真圆裁切；不得顺手改成多边形近似。
- 同时检查 `main.py` 的标量物理与 `src/app/flow_numpy.py` 的向量化路径，保留随机调用顺序和等价性。
- 未验证的合批实验不默认启用；取消、跳过、未完成的测试不得记作通过。
- 测试不读写个人配置；抓图不得混入 FPS 评分。
- 临时输出放 `_temp/`。不使用 `git add -A` 把暂存资料和未跟踪设计页一起提交。

## 验证与发布

结论注明证据等级：E1 有对照实测，E2 无对照实测，E3 代码检查，E4 已被推翻。
没到 E1，不说“验证通过”。判据必须先标定，检查必须有完成标记，不能静默跳过。

push 前递增 `src/app/app_version.py`，APK 和 Benchmark 共用 `APP_VERSION`。
提交标题以版本号开头，后续中文；运行版本检查、静态检查和 `bash src/tools/_prepush.sh`。
push 后用 `src/tools/_watch_build.sh` 监视构建并报告结果。

构建版本锁和方向设置以 `buildozer.spec` 为准，不擅自升级：
Kivy 2.3.0、p4a v2024.01.21、Buildozer 1.5.0、Cython < 3、host Python 3.10。

修改视觉前查阅 [图文用例](design/checks/rules/sand-flow.html)、[历史验收依据](design/checks/notes/old-criteria.md) 和 [踩坑经验](docs/technical/lessons.md)。
技术背景按需阅读，不把历史文件中的旧版本、行号、已撤回方案当成现状。
