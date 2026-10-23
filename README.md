# 跳跳的沙漏

Python + Kivy 沙漏计时应用，NumPy 处理粒子和材质数据。PC 用于运行和验证，Android APK 由 GitHub Actions 构建。
版本唯一来源是 `src/app/app_version.py`，不在文档中另写版本号。

当前3.11候选证据见 `design/checks/records/v3.11/`，是否放行以 `design/checks/current-review.json` 为准。
按用户要求恢复真实3.3提交 `ff3b721` 的完整飞溅初速，保留空中浅色颗粒、原发射量及粒径；
只在下降接近堆面或触地后，逐渐转向沙堆本色和出生后固定的轻微色差，消除密集散粒叠成的亮圈。
普通Rectangle与批量绘制共用RGBA8颜色，3.10的贴坡、埋藏不复活和暂停时钟修复继续保留。
主沙柱、颈宽、沙量和计时不回退；本轮不声称整套画面逐像素复刻3.3。
颈内净宽仍为1～5秒15%、10小时及以上3%，中间作幂插值，容量保留100%。
3.8的工程检查证据继续保留，但其整体细沙质感在续审中未达到目标，不能据旧通过记录放行新外观。
当前发布由 `check_flow_release.py` 核对原始连续帧、5秒/50秒真实时间录像、PC 工作耗时与源码身份；
`design/checks/current-review.json` 是唯一当前审核决定，历史通过记录不能覆盖撤回或待验状态。
历史设备取图和源码覆盖记录单独保留，不能代替当前 PC 验收或正式 APK 安装证明。

## PC 运行

```powershell
pip install -r src/app/requirements-pc.txt
python main.py
```

沙漏支持开始、暂停、重置、周期和配色设置、循环音效、完成播报及内置 Benchmark。
自动取图使用真正隐藏的 PC 窗口和独立设置目录，不连接或操作 MuMu、ADB 或真机。

## 前台常亮

应用在前台时禁止自动息屏，包括未开始、计时中、暂停、完成和 Benchmark。
Android 复用弹珠项目的 UI 线程 `FLAG_KEEP_SCREEN_ON` 窗口标志，不需要唤醒锁权限；
Windows 同时阻止显示器及系统空闲休眠。切后台或退出恢复系统策略，返回前台重新申请。
不阻止用户手动锁屏、电源键或系统强制关机。历史 Pymunk 工具和记录保留供查阅，不再接入应用。

## 开发检查

```powershell
python src/tools/run_regression.py --plan
python src/tools/run_regression.py --profile core
```

截图和桌面 Benchmark 输出在 `_temp/benchmark_logs/`。测试配置使用独立临时目录，不改个人设置。
PC 结果不能直接当成 Android 性能结论；检查范围与发布条件见
[改完以后怎么检查](design/checks/README.md)、[QA 纪律](design/checks/notes/qa-rules.md)。
外观改动只回归本次外观及相邻连接；本轮另包含用户已要求的同场 PC 单帧工作量对照。
本次发布用 `check_flow_release.py` 核对原始证据和运行源码，并要求当前审核决定已批准且严重问题全部关闭。
本轮按已发布3.10提交 `c869e24` 做同配置对照，飞溅发射另与真实3.3提交 `ff3b721` 核对。
颜色10项、批量绘制13项和3.3初速5项检查触地调色、普通/batch一致及完整力度，
发布守卫检查精确改动边界；接触、材料、遮挡、连续性及旧视觉负例继续保留。
3.10交接亮圈已被用户反馈重新打开，须由3.11正常尺寸和动态证据关闭，不能沿用旧批准。
旧 `_splash_golden.py` 用于存储路径等价对照，保留原指纹，不重存基线，也不以它宣称本次接触模型未变。
完整飞溅与触地融合须由新反例、实际正常尺寸画面和5秒/50秒动态过程验收。
5秒首次落地后的沙堆增长偏快仍列为既有P2，本轮未改该时序，不能据本轮结果声称全应用无问题。
性能对照计时包括更新、绘制和 GPU 完成等待，不包含截图编码，不等于真实屏幕或 Android FPS，
也不构成音频或完整应用验收。接收区整体发生像素变化不能单独证明表层纹理在动；
`verify_receiving_pixels.py` 固定几何及其他时钟，单独检查接收层相位和关闭该层的负对照。
历史材质、形状和回退检查保留供对应版本查阅。
`test_keep_awake.py` 检查 Android UI 线程、窗口标志和时序，`verify_keep_awake_runtime.py`
检查隐藏 PC 应用生命周期和 Windows 原生请求；这些不是 Android 设备、音频或帧率验收。

## 构建

推送到 `main` 触发 `.github/workflows/build-apk.yml`，生成 debug APK，并发布到 GitHub Release。
Windows 不进行 Android SDK 构建。

每次 push 前递增 `src/app/app_version.py`，提交标题以新版本号开头，其余使用中文。
先运行版本检查和 `bash src/tools/_prepush.sh`；失败或未完成的检查不能记为通过。
推送钩子还执行 `release_decision.py --committed`，要求当前审核决定及其记录目录中的证据
已纳入待推送的 HEAD，且工作区内容按 Git 的路径清理规则与提交一致；未提交的批准不能放行。

## 目录

| 路径 | 用途 |
|---|---|
| `main.py` / `src/app/` | 应用入口、版本、物理、材质、渲染与帧率测试 |
| `src/build/recipes/` | Pymunk 的 Android 编译配方，不作为应用资源装入 APK |
| `sounds/` | 播放音频、语音词块；`source/` 保存本地原始素材 |
| `ui/` | 图标、界面图片、中文字体 |
| `src/tools/` | 构建、验证、诊断与素材加工工具，不进入 APK |
| `docs/` | 文件索引、踩坑经验和决策记录 |
| `design/` | 设计稿、改动记录、配图、测试规则、历史素材 |
| `_temp/` | 日志、截图、缓存、过期文档及旧探针 |

查找文件用途见 [逐文件索引](docs/file-index.md)；其他说明见 [目录维护](docs/structure.md)、
[文档索引](docs/README.md) 和 [工具索引](src/tools/README.md)。
索引检查已接入本地 push、GitHub push / PR 和每日北京时间 09:00 的 CI 定时任务。
