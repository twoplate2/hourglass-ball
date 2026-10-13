# 目录维护

## 当前结构

```text
ShaLou/
  main.py
  buildozer.spec
  README.md / AGENTS.md / CLAUDE.md
  .git/                 Git 元数据，不能任意改名或移动
  .github/              GitHub Actions 固定入口
  .githooks/            本地推送钩子
  sounds/
    *.wav               实际播放的音频
    voice/              完成播报词块
    source/             原始 MP3，本地保留、不打包
  ui/
    *.png               图标、启动占位图、滑杆贴图
    fonts/              中文字体
  src/
    app/                应用运行模块、版本和内置 Benchmark
    tools/              构建、测试、诊断、素材加工，不进入 APK
  docs/                 文件索引、经验与决策记录
  design/
    pages/              当前安卓界面设计说明（hourglass.html）
    history/            版本对照、误诊记录与候选方案
    images/             设计页共用图片与正式保留的安卓截图
    checks/
      rules/            按部位组织的图文用例、机器清单与执行说明
      records/          按版本留图、日志与未测项；旧记录绑定规则快照
      notes/            版本变化、证据纪律、历史判据与教训
    old-assets/         停用启动图
  _temp/
    benchmark_logs/     截图、报告、视觉参照臂
    retired-docs/       过期文档，待用户确认删除
    retired-tools/      旧实验工具，待用户确认删除
    kivyhome-legacy/    旧测试配置，可删除
    pycache/            验证时的 Python 缓存，可删除
    regression-runs/    每轮回归日志、报告与人工证据模板
```

## 已收拢的目录

- `mp3/` 合并到 `sounds/source/`，根目录沙沙声合并到 `sounds/sand_loop.wav`。
- `app_version.py`、`frame_benchmark.py` 收入 `src/app/`；版本检查兼容迁移前的 Git 历史。
- `fonts/` 合并到 `ui/fonts/`，根目录图标合并到 `ui/icon.png`。
- `p4a/hook.py` 合并到 `src/tools/android_hook.py`，打包配置同步修改。
- 停用的 `presplash.png` 放到 `design/old-assets/`，不当作运行资源。
- `_kivyhome/` 改为各探针使用独立临时目录，旧副本暂存。
- `benchmark_logs/` 放到 `_temp/benchmark_logs/`；Android 私有数据目录的日志位置保持不变。
- `_shot/` 的取图输出统一写到 `_temp/benchmark_logs/`，不再在根目录生成截图目录。
- `Temp/`、迁移留下的空目录和 Python 缓存可以清理。普通 Python 运行可能重新生成 `__pycache__/`。

## 哪些不能合并

`.git/` 和 `.github/` 是工具固定识别的位置；`.githooks/` 已由 Git 配置使用，保留独立入口。
`design/` 保存设计、审阅页和测试规则，`docs/` 保存文件索引及技术经验，正式规则只存一份。
脚本仍在 `src/tools/`，输出仍在 `_temp/`；不能把测试代码塞进设计稿目录。
测试与发布入口见 [改完以后怎么检查](../design/checks/README.md)。
`design/` 下所有目录和文件名统一英文，正文继续使用中文；本次命名调整不改变检查要求。

应用动态加载的模块已归入 `src/app/`，文件名带 `experiment` 不代表可以删。
`src/tools/` 仅保留开发工具，职责和限制见 [逐文件索引](file-index.md)。

## 临时区与清理

`_temp/`、`docs/generated/`、`sounds/source/` 不进入 Git，并由 `buildozer.spec` 显式排除打包。
`.gitignore` 只控制 Git，不是 APK 的文件筛选器。

`src/tools/_temp.py` 默认只列出或预览；真正删除需要 `--go`。
`benchmark_logs/`、`retired-docs/`、`retired-tools/` 不参与自动清理候选，用户可手动审阅后删除。
日志中的视觉参照臂供发布检查使用；删除不影响应用运行，但会使缺少参照的检查明确失败。

运行时模块和开发工具已分开。进一步拆分源码可以考虑拆开 `main.py` 的音频、界面、
物理与渲染。那属于代码重构，需要专门的回归验证，不与这轮文件整理混在一起。

## 本轮验证

2026-10-13，仅 PC：

- 目录、资源、索引检查 8 项和版本规则 5 项完成；55 个 Python 文件无未定义名字告警。
- 物理标量/向量化、端点打包对照结果一致。
- 同参数、同时刻迁移前后 2 张图片逐像素相同，不屏蔽任何区域（E1，仅此采样点）。
- 迁移的 4 个音频/图片文件逐字节相同；暂存文档及 94 个旧工具保留内容（E1）。
- 调用 Buildozer 1.5.0 的文件收集方法，应用资源包含、开发目录排除，并跑了正负对照（E1）。
- `verify_sand_finish.py` 仍有 47 项失败；整理前代码跑相同 15 个用例得到完全相同的结果（E1：旧问题未修，不计通过）。

原始收尾结果在 `_temp/benchmark_logs/sand_finish_before/suite.json` 和
`_temp/benchmark_logs/legacy-shot/sand_finish/suite.json`。本轮没有编译 APK、连接设备或推送代码。
