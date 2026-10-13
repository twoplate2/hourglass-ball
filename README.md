# 跳跳的沙漏

Python + Kivy 沙漏计时应用，使用 NumPy 处理粒子。PC 用于运行和验证，Android APK 由 GitHub Actions 构建。
版本唯一来源是 `src/app/app_version.py`，不在文档中另写版本号。

## PC 运行

```powershell
pip install kivy==2.3.0 numpy pillow pyflakes
python main.py
```

沙漏支持开始、暂停、重置、周期和配色设置、循环音效、完成播报及内置 Benchmark。
自动化验证不在用户本机桌面启动 PC 测试；应用运行验证使用用户预留的 root MuMu，
只操作指定实例，不连接真机。PC 检查脚本交给 CI 执行。

## 开发检查

```powershell
python src/tools/run_regression.py --plan
python src/tools/run_regression.py --profile core
```

截图和桌面 Benchmark 输出在 `_temp/benchmark_logs/`。测试配置使用独立临时目录，不改个人设置。
PC 结果不能直接当成 Android 性能结论；检查范围与发布条件见
[改完以后怎么检查](design/checks/README.md)、[QA 纪律](design/checks/notes/qa-rules.md)。
外观改动只回归本次外观及相邻连接；帧率、音频和完整回归由用户另行安排。
本次推送核对已有 MuMu 外观证据、源码身份和版本，不把基础检查当完整验收。

## 构建

推送到 `main` 触发 `.github/workflows/build-apk.yml`，生成 debug APK，并发布到 GitHub Release。
Windows 不进行 Android SDK 构建。

每次 push 前递增 `src/app/app_version.py`，提交标题以新版本号开头，其余使用中文。
先运行版本检查和 `bash src/tools/_prepush.sh`；失败或未完成的检查不能记为通过。

## 目录

| 路径 | 用途 |
|---|---|
| `main.py` / `src/app/` | 应用入口、版本、物理、材质、渲染与帧率测试 |
| `sounds/` | 播放音频、语音词块；`source/` 保存本地原始素材 |
| `ui/` | 图标、界面图片、中文字体 |
| `src/tools/` | 构建、验证、诊断与素材加工工具，不进入 APK |
| `docs/` | 文件索引、踩坑经验和决策记录 |
| `design/` | 设计稿、改动记录、配图、测试规则、历史素材 |
| `_temp/` | 日志、截图、缓存、过期文档及旧探针 |

查找文件用途见 [逐文件索引](docs/file-index.md)；其他说明见 [目录维护](docs/structure.md)、
[文档索引](docs/README.md) 和 [工具索引](src/tools/README.md)。
索引检查已接入本地 push、GitHub push / PR 和每日北京时间 09:00 的 CI 定时任务。
