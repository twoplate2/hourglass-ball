# 跳跳的沙漏

Python + Kivy 沙漏计时应用，使用 Pymunk 处理颈部、沙柱与落地颗粒碰撞，NumPy 处理材质等数据。PC 用于运行和验证，Android APK 由 GitHub Actions 构建。
版本唯一来源是 `src/app/app_version.py`，不在文档中另写版本号。

## PC 运行

```powershell
pip install -r src/app/requirements-pc.txt
python main.py
```

沙漏支持开始、暂停、重置、周期和配色设置、循环音效、完成播报及内置 Benchmark。
只在 PC 上运行和验证，不操作 MuMu、ADB 或真机。自动取图使用隐藏窗口和独立设置目录。

## 沙粒接入

沿用 06 的两层独立 2D 颗粒碰撞，保留上下沙体的计时体积；不是整只沙漏的 3D 模拟。
出口附近、下落沙粒和落地接触由 Pymunk 求解，上半颈部延续沙体材质和计时体积。
边缘来自真实颗粒的位置，没有额外摇摆或固定沙柱轮廓；细颈不重复逐颗模拟上方宽漏斗中的沙体。
粒径按有效瓶颈内宽缩放，颗粒档位同步影响沙体纹理和移动沙粒，颜色纹理共享。
当前摩擦为 0.90、回弹为 0.03，入口速度为 35、重力为 450，坐标按内球半径 166 归一化。
通常按 120 Hz 求解，细粒自动缩短步长；极短计时缩时播放，暂停一起冻结。
计时沙量守恒；只有已经全部落入下沙体后，才清理剩余的无沙量代理粒。

## 开发检查

```powershell
python src/tools/run_regression.py --plan
python src/tools/run_regression.py --profile core
```

截图和桌面 Benchmark 输出在 `_temp/benchmark_logs/`。测试配置使用独立临时目录，不改个人设置。
PC 结果不能直接当成 Android 性能结论；检查范围与发布条件见
[改完以后怎么检查](design/checks/README.md)、[QA 纪律](design/checks/notes/qa-rules.md)。
外观改动只回归本次外观及相邻连接；帧率、音频和完整回归由用户另行安排。
本次 Pymunk 接入使用 `python -B src/tools/verify_pymunk_integration.py` 核对真实应用，
推送前用 `check_pymunk_release.py` 核对完整的专项结果、外观记录、源码身份和版本。
它不是 Android、音频或帧率验收；上下沙体仍使用现有计时体积模型，不宣称整只沙漏是 3D 物理。

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
