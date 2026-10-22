# 跳跳的沙漏

Python + Kivy 沙漏计时应用，已恢复 3.0 的沙流、计时和渲染实现，NumPy 处理材质等数据。PC 用于运行和验证，Android APK 由 GitHub Actions 构建。
版本唯一来源是 `src/app/app_version.py`，不在文档中另写版本号。

当前版本包含界面与材质改进：暂停/完成按钮文案、原色设置预览、清晰滑杆，
沙柱的每个短粒跟随实际粒子连续下落，材质随颗粒移动，不再用整束波浪轮廓裁切；
颗粒只从出口进入画面，落入沙堆后被遮挡。落点飞溅集中在堆顶附近，表层纹理沿坡铺开。
沙量、主沙粒运动和玻璃轮廓保持原模型，飞溅装饰的轨迹按上述目标调整。
当前发布由 `check_particle_release.py` 核对粒子连续性、外观、完整过程、PC 工作耗时与源码身份。
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
外观改动只回归本次外观及相邻连接；帧率、音频和完整回归由用户另行安排。
本次发布用 `check_particle_release.py` 重放已审阅的 PC 外观对照并核对全部运行源码。
性能对照计时包括更新、绘制和 GPU 完成等待，不包含截图编码，不等于实屏或 Android FPS。
历史材质、形状和回退检查保留供对应版本查阅。
`test_keep_awake.py` 检查 Android UI 线程、窗口标志和时序，`verify_keep_awake_runtime.py`
检查隐藏 PC 应用生命周期和 Windows 原生请求；这些不是 Android 设备、音频或帧率验收。

## 构建

推送到 `main` 触发 `.github/workflows/build-apk.yml`，生成 debug APK，并发布到 GitHub Release。
Windows 不进行 Android SDK 构建。

每次 push 前递增 `src/app/app_version.py`，提交标题以新版本号开头，其余使用中文。
先运行版本检查和 `bash src/tools/_prepush.sh`；失败或未完成的检查不能记为通过。

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
