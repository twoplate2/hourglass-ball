# 文件用途索引

APK版本以 src/app/app_version.py 的 APP_VERSION 为准。GitHub仅发布APK相关代码、回归测试与构建配置；外观验收录像、截图和归档留在本地。以下旧版验收页面仅为历史记录，不代表当前版本已在Android、音频或性能方面验收。

本轮出口衔接与宽度专项工具：

- [measure_neck_stream_seam.py](../src/tools/measure_neck_stream_seam.py)：出口两侧颜色与纹理诊断，不判视觉通过。
- [test_neck_stream_seam.py](../src/tools/test_neck_stream_seam.py)：材质阶跃、坐标换算及既有失败证据测试。
- [verify_neck_stream_join.py](../src/tools/verify_neck_stream_join.py)：隐藏PC同态材质过渡消融、共享取色和静态回退检查。
- [measure_stream_width.py](../src/tools/measure_stream_width.py)：原生连续帧近场主体宽度及下段局部掐腰诊断，独立沙柱通道排除飞溅。
- [test_stream_width.py](../src/tools/test_stream_width.py)：硬肩、平滑收束、孤粒、截断和图像篡改反例。
- [test_stream_sampling.py](../src/tools/test_stream_sampling.py)：APK沙流出生覆盖、每局相位与启动速度平滑过渡的回归检查。
- [test_stream_contact_ceiling.py](../src/tools/test_stream_contact_ceiling.py)：碗底两侧碰撞粗筛回归，避免粒子被玻璃边界突然夹回。
- [check_outlet_release.py](../src/tools/check_outlet_release.py)：3.12限定外观发布证据、源码范围、真实图像与独立审核身份门禁。
- [test_outlet_release.py](../src/tools/test_outlet_release.py)：3.12证据、宽度、取色消融及源码范围的发布反例。

当前维护的源码和工具逐项列在这里。新增、移动、删除文件时同步更新；
`test_repository_layout.py` 会检查源码是否漏登记、链接是否失效。
说明来自代码职责，不等于这里的所有工具都已在本轮实测。

自动检查在本地 push 前、GitHub push / PR 时执行，也在每天北京时间 09:00 定时执行。
GitHub 定时任务需要配置推送到默认分支后才生效，实际启动时间可能由平台延迟。
检查只能发现登记和路径问题，不能自动证明用途描述或历史测试门槛仍正确。

## 根目录入口

| 文件 | 用途 |
|---|---|
| [main.py](../main.py) | Kivy 启动入口，沙漏界面、物理协调、渲染与音频控制；APK 必须有此入口 |
| [buildozer.spec](../buildozer.spec) | APK 构建配置、依赖锁、版本来源、资源白名单和排除目录 |
| [README.md](../README.md) | 项目运行与文档入口 |
| [AGENTS.md](../AGENTS.md) | 当前项目工作规则 |
| [CLAUDE.md](../CLAUDE.md) | 简短开发规则与历史资料入口 |
| [handoff-current.txt](../handoff-current.txt) | 当前交接入口；正文按 handoff 技能存入系统 `%TEMP%`，不进入 APK |
| [.gitignore](../.gitignore) | 忽略输出、原始素材和缓存，不控制 APK 打包 |
| [.gitattributes](../.gitattributes) | Git 行尾等文件属性 |
| [.githooks/pre-push](../.githooks/pre-push) | 核对当前审核、已提交证据和运行源码、关联检查及版本标题；不据此宣称完整应用验收 |
| [.github/workflows/build-apk.yml](../.github/workflows/build-apk.yml) | GitHub Actions 构建、检查、发布 |
| [.github/workflows/check-file-index.yml](../.github/workflows/check-file-index.yml) | 每日北京时间 09:00 或手动检查索引和目录布局；推送时复用打包内的基础检查，不重复触发独立任务 |
| [.github/workflows/check-regression.yml](../.github/workflows/check-regression.yml) | CI 目录、版本、语法和资料基础检查；失败拦截构建，不执行完整应用回归 |

## 应用模块

这些文件进入 APK，不作为可删除的临时工具。带 `experiment` 的文件名是历史命名，
并不代表它未被应用使用。可选路径不因此默认开启。

| 文件 | 用途 |
|---|---|
| [__init__.py](../src/app/__init__.py) | 应用 Python 包入口 |
| [app_version.py](../src/app/app_version.py) | APK、Benchmark 和复制报告共用的 `APP_VERSION` |
| [keep_awake.py](../src/app/keep_awake.py) | 前台防息屏，Android UI 线程窗口常亮及 Windows 空闲休眠阻止；后台和退出释放 |
| [pymunk_sand.py](../src/app/pymunk_sand.py) | 06 接入游戏后的真实颗粒碰撞、颈部供沙、下沙接触与粒子绘制；上下沙体保留计时体积模型 |
| [requirements-pc.txt](../src/app/requirements-pc.txt) | 当前 PC 运行依赖；回退后不需要 Pymunk |
| [frame_benchmark.py](../src/app/frame_benchmark.py) | 帧间隔采样、统计、曲线、报告与测试状态恢复 |
| [flow_numpy.py](../src/app/flow_numpy.py) | 主沙流的向量化物理内核，与标量路径保持等价 |
| [splash_motion.py](../src/app/splash_motion.py) | 飞溅弹道、接触后贴坡阻尼、涨堆埋藏停稳和重新入流剔除；复用原粒子身份 |
| [short_grain_stream.py](../src/app/short_grain_stream.py) | 持续短粒渲染、固定微粒细节和实际沙面遮挡；3.10保留已发布3.9实现 |
| [sand_flow_material.py](../src/app/sand_flow_material.py) | 沙体、颈部、接触面的 GPU 材质 |
| [flow_batch_experiment.py](../src/app/flow_batch_experiment.py) | 批处理几何、缓冲区和各渲染器共用实现 |
| [flow_texture_experiment.py](../src/app/flow_texture_experiment.py) | 端点纹理沙流渲染路径 |
| [stream_rendering.py](../src/app/stream_rendering.py) | 沙柱绘制密度与落点补齐的共用计算；不改变粒子物理 |
| [grain_style.py](../src/app/grain_style.py) | 沙柱材质的有界时间相位和局部落点风格参数；不推进粒子或改变沙量 |
| [flow_gpu_experiment.py](../src/app/flow_gpu_experiment.py) | 可选择的 GPU 平移渲染路径 |
| [flow_splash_experiment.py](../src/app/flow_splash_experiment.py) | 飞溅、颈部颗粒、闪光的批处理实现 |
| [marker_batch_experiment.py](../src/app/marker_batch_experiment.py) | 沙面标记的可选批处理；不能擅自默认开启 |
| [prof_android.py](../src/app/prof_android.py) | 主程序加载的可选函数耗时挂点，无标记文件时不采样 |
| [_tail_selfcheck.py](../src/app/_tail_selfcheck.py) | 主程序调用的可选末段自检，按标记文件触发 |

## 构建与维护

除 `.sh` 外，命令形式为 `python src/tools/<文件名> [参数]`，在仓库根目录运行。
Android hook 由打包器调用；shell 工具使用 Git Bash。此目录不装入 APK。

| 文件 | 用途与限制 |
|---|---|
| [android_hook.py](../src/tools/android_hook.py) | 构建时给 Manifest 注入方向与可缩放属性，不直接启动 |
| [check_rollback_release.py](../src/tools/check_rollback_release.py) | 对照 3.0 提交逐模块验证回退，主程序仅允许常亮钩子变化 |
| [test_keep_awake.py](../src/tools/test_keep_awake.py) | 防息屏失败用例：UI 线程、嵌套常量、排队竞态、恢复、Windows 双请求和错误日志 |
| [test_ui_polish.py](../src/tools/test_ui_polish.py) | 无窗口核对开始/继续/重新开始及 Benchmark 文案、暂停设置预览的打开与关闭恢复、沙纹浓淡滑杆原有参数映射；不代替界面实显检查 |
| [test_grain_style.py](../src/tools/test_grain_style.py) | 无窗口检查材质相位回绕与冻结、落点局部范围及连续边界、真实材质 uniform 的时钟更新；不替代着色器实显验收 |
| [material_visual_probe.py](../src/tools/material_visual_probe.py) | 材质改动的同种子、同逻辑时刻取图探针；供实际渲染对照，不代表实时动态或性能结论 |
| [compare_material_frames.py](../src/tools/compare_material_frames.py) | 离线比较材质探针的同配置帧、粒子与沙量不变量和局部改动范围；缺项记为待验，不自动批准新外观 |
| [verify_keep_awake_runtime.py](../src/tools/verify_keep_awake_runtime.py) | 真正隐藏窗口及独立设置的实际应用生命周期检查，Windows 核对原生电源请求 |
| [Pymunk 构建配方](../src/build/recipes/pymunk/__init__.py) | Android 交叉编译 Pymunk 7.3，不沿用 p4a 内置的 6.0 配方；配方目录不打入 APK |
| [verify_pymunk_integration.py](../src/tools/verify_pymunk_integration.py) | 隐藏 PC 窗口验证真实应用的新碰撞引擎、计时沙量、暂停重置收尾及相邻画面；不测试帧率或音频 |
| [check_pymunk_release.py](../src/tools/check_pymunk_release.py) | 推送前检查 PC 接入测试、已审阅配图、源码摘要与已提交内容；只递增版本时绑定原测试提交复用结果，不接受沙流源码变化、部分或中断测试 |
| [test_pymunk_release.py](../src/tools/test_pymunk_release.py) | 新专项发布检查的正反例：通过、漏测、失败、空图、路径越界、过期报告与未提交源码 |
| [check_release_version.py](../src/tools/check_release_version.py) | 检查 `--base` / `--head` 的版本递增与中文标题，兼容旧目录历史 |
| [test_release_version.py](../src/tools/test_release_version.py) | 无窗口测试版本规则和统一版本来源 |
| [test_repository_layout.py](../src/tools/test_repository_layout.py) | 检查资源路径、文档链接、文件索引和暂存区隔离 |
| [_prepush.sh](../src/tools/_prepush.sh) | 兼容 shell 入口，执行当前沙流证据、关联反例、防息屏和基础检查；不自动运行完整应用回归 |
| [run_regression.py](../src/tools/run_regression.py) | 关联测试选择、完整执行、完成检查、报告摘要与人工证据核对；PC 限定 |
| [test_regression_gate.py](../src/tools/test_regression_gate.py) | 正负对照验证漏测、超时、失败、旧报告和坏图片确实会阻挡 |
| [check_design_pages.py](../src/tools/check_design_pages.py) | 解析所有设计 HTML，检查图片解码、相对链接与页内锚点；浏览器实显仍需另验 |
| [build_sand_flow_docs.py](../src/tools/build_sand_flow_docs.py) | 生成图文规则、逐编号版本记录和索引；可创建默认全未测的新版本记录，核对证据摘要，不启动应用或运行测试 |
| [capture_sand_flow_media.py](../src/tools/capture_sand_flow_media.py) | 固定预留 MuMu 采集指定版本；核对 APK、源码或安装版身份及配置，录完整周期和暂停恢复，采集结束保持暂停 |
| [deploy_mumu_source.py](../src/tools/deploy_mumu_source.py) | 备份预留 MuMu 应用，再覆盖源码和资源、回读摘要；不是新 APK 安装证明 |
| [run_mumu_checks.py](../src/tools/run_mumu_checks.py) | 在指定 MuMu 运行公式、配置取图或真实帧率检查，校验源码/原 APK 对照身份，结束恢复正常入口；不启动 PC 应用 |
| [check_visual_review.py](../src/tools/check_visual_review.py) | 按用户要求核对本次外观范围的 MuMu 证据、源码身份和未改区域，供推送钩子使用；不是完整或性能验收 |
| [mumu_probe_entry.py](../src/tools/mumu_probe_entry.py) | 仅供上述工具临时放入模拟器的入口，调用真实应用与 BenchmarkRunner；矩阵取图标明逻辑推进，不是实时过程证明 |
| [compare_mumu_frames.py](../src/tools/compare_mumu_frames.py) | 离线比较 MuMu 同种子同进度前后图、粒子数与沙量账本，标出预期区域之外差异；不自动验收改动区域，也不重写基线 |
| [prepare_sand_flow_media.py](../src/tools/prepare_sand_flow_media.py) | 离线裁出真实原速短循环、阶段配图与逐编号素材记录；保留未验收状态，原始录屏移入暂存区 |
| [prepare_sand_flow_review.py](../src/tools/prepare_sand_flow_review.py) | 离线整理修复轮次的原速录像、出口/边缘循环和连续帧拼图；大原片归档暂存区，不判通过 |
| [_watch_build.sh](../src/tools/_watch_build.sh) | 以 run 基线监视 GitHub 构建，网络失败不代表构建成功 |
| [_temp.py](../src/tools/_temp.py) | `list` / `sweep` / `clear`；默认预览，`--go` 才删除，不清理用户待审阅资料 |
| [_pack_source_md.py](../src/tools/_pack_source_md.py) | 生成 `docs/generated/source.md`，只含当前运行代码白名单 |
| [README.md](../src/tools/README.md) | 工具分类与常用入口 |

## 回归与性能

这些是独立 CLI，不被 import 不代表无用。截图不能充当 FPS 成绩；
本轮没有重标定全部历史视觉门槛。

| 文件 | 用途与限制 |
|---|---|
| [verify_hourglass.py](../src/tools/verify_hourglass.py) | PC 综合回归和三周期 Benchmark；部分对照依赖历史参考源码 |
| [verify_sand_finish.py](../src/tools/verify_sand_finish.py) | 沙量守恒、暂停、重置、长帧、收尾、Benchmark 恢复；可渲染末段 |
| [verify_completion_popup.py](../src/tools/verify_completion_popup.py) | 完成弹窗文案与排版人工预览，不能当作无人值守测试 |
| [verify_dev_levels.py](../src/tools/verify_dev_levels.py) | 开发者菜单档位的逻辑/画面检查，失败或未完成返回非零 |
| [verify_grain_levels.py](../src/tools/verify_grain_levels.py) | 检查六档可见差异和实际尺寸，失败/空测量不放行；历史门槛需确认适用性 |
| [verify_particle_hot_loop.py](../src/tools/verify_particle_hot_loop.py) | 物理热循环状态等价与 CPU 成本，需要 `--before` 参考；不是 FPS |
| [test_physics_equiv.py](../src/tools/test_physics_equiv.py) | NumPy 与标量参考逐位等价 |
| [test_pack_equiv.py](../src/tools/test_pack_equiv.py) | 向量化端点打包与逐粒子打包的字节等价 |
| [test_stream_rendering.py](../src/tools/test_stream_rendering.py) | 使用真实共用公式检查开局密度、局部落点补齐、收尾及标量/向量/分桶等价；不代表视觉验收 |
| [smoke_splash_origin.py](../src/tools/smoke_splash_origin.py) | 接触位置、宽度相关飞溅密度的短 PC 烟测 |
| [profile_frames.py](../src/tools/profile_frames.py) | 真实 Kivy flip 间隔统计，不读写个人设置 |
| [_hot_desktop.py](../src/tools/_hot_desktop.py) | 用 cProfile 测稳态热点；PC 排名不能直接代替 Android 成本 |
| [analyze_ab.py](../src/tools/analyze_ab.py) | 分析已有 A/B 报告，按轮配对；只读离线数据，不连接设备 |
| [flow_chunk_experiment.py](../src/tools/flow_chunk_experiment.py) | 保留 Line、按块挂载画布的 PC 可选实验；不属于应用运行模块 |

## 视觉诊断

通常读取 `_temp/benchmark_logs/flow_visual_<标签>/`；缺少输入时应先生成配套截图。
这些单项工具只覆盖各自的判据，不代表整个应用验证完成。

| 文件 | 用途与限制 |
|---|---|
| [inspect_flow.py](../src/tools/inspect_flow.py) | 固定种子、时刻和尺寸取图与测量；输出 `flow_visual_<标签>` |
| [_pixdiff.py](../src/tools/_pixdiff.py) | 两个取图目录逐像素对比，可屏蔽版本号；没有图片不算成功 |
| [_side_by_side.py](../src/tools/_side_by_side.py) | 同时刻、同尺寸截图生成并排图，给用户审阅 |
| [_diff_map.py](../src/tools/_diff_map.py) | 用 ASCII 和差异图定位不同像素的形状 |
| [_render_golden.py](../src/tools/_render_golden.py) | `--check` 核对渲染指纹；`--save` 改基线需明确依据 |
| [_splash_golden.py](../src/tools/_splash_golden.py) | 历史飞溅存储路径等价指纹；保留原指纹，不重存基线，不据此声称3.10接触模型未变 |
| [render_golden.fp.json](../src/tools/render_golden.fp.json) | Git 跟踪的渲染参考指纹，不是可再生垃圾 |
| [splash_golden.fp.json](../src/tools/splash_golden.fp.json) | Git 跟踪的飞溅参考指纹，不是普通输出 |
| [_accept_check.py](../src/tools/_accept_check.py) | `design/checks/notes/old-criteria.md` 的 A1-A7；标定依赖指定好/坏标签截图 |
| [_xz_geom_accept.py](../src/tools/_xz_geom_accept.py) | 下球沙堆几何判据，旧阈值不自动适用于新视觉 |
| [_probe_geom_invariants.py](../src/tools/_probe_geom_invariants.py) | 多种实际尺寸下的几何不变量 |
| [_probe_neck_batch_equiv.py](../src/tools/_probe_neck_batch_equiv.py) | 颈部 Line 与批处理几何、颜色等价，不证明像素一致 |
| [_probe_grain_motion.py](../src/tools/_probe_grain_motion.py) | 相邻帧互相关估计颗粒纹理运动，不能跨重排点乱配帧 |
| [_probe_speed_ratio.py](../src/tools/_probe_speed_ratio.py) | 图案下移速度与粒子速度的对照诊断 |
| [_probe_gap_structure.py](../src/tools/_probe_gap_structure.py) | 沙柱连续、断续的空间结构诊断 |
| [_probe_stream_ink.py](../src/tools/_probe_stream_ink.py) | 沙流与沙堆的墨量、亮度剖面 |
| [_probe_ink_cover.py](../src/tools/_probe_ink_cover.py) | 查看墨量覆盖率闸在不同尺寸下的作用 |
| [neck_ab.py](../src/tools/neck_ab.py) | 读取 A/B 颈部裁图，拼接、放大、叠加差分 |
| [neck_density.py](../src/tools/neck_density.py) | 在固定颈部窗口测可见沙密度 |
| [neck_profile.py](../src/tools/neck_profile.py) | 逐行统计颈部颜色游程、沙宽和玻璃边界 |
| [sand_material_ab.py](../src/tools/sand_material_ab.py) | 运行时替换材质候选生成对照，不改正式源码 |

## 素材与资料

| 文件或目录 | 用途 |
|---|---|
| [make_clock_loop.py](../src/tools/make_clock_loop.py) | 从本地 `sounds/source/zhongbiao.mp3` 按拍加工 `sounds/clock.wav` |
| [generate_completion_voice.py](../src/tools/generate_completion_voice.py) | 使用在线 TTS 重新生成完成播报，需要开发依赖；会写入播放资源 |
| [generate_voice_tokens.py](../src/tools/generate_voice_tokens.py) | 重新生成拼接播报词块，需要在线 TTS；会写入 `sounds/voice/` |
| `sounds/*.wav` | 沙沙、水流、风、钟表和完成播报的播放素材 |
| `sounds/voice/*.wav` | 按数字、单位、短语和档位分块的 90 个预录词块，不需联网播放 |
| `sounds/source/` | 本地原始音效，删除不影响播放，但会失去重新加工的输入 |
| `ui/icon.png` / `ui/startup_blank.png` | 启动器图标、启动遮罩占位 |
| `ui/slider_track.png` / `ui/slider_cursor.png` | 滑杆轨道贴图及保留的旧游标；当前游标由 `main.py` 中的 `_SmoothSlider` 按实际尺寸绘制 |
| `ui/fonts/NotoSansSC-Medium.otf` | 应用中文字体 |
| [ui/shazhu/index.html](../ui/shazhu/index.html) | 新轮子与五种旧沙柱的 10 秒预览选择页；只是独立外观草案，不是应用录像或验证结果 |
| [pymunk_grains.py](../ui/shazhu/pymunk_grains.py) | 06 Pymunk 实算预览：上下沙与沙流均为碰撞颗粒，无指定沙柱轮廓或额外飞溅；默认两层独立二维投影，不是三维求解，10 秒片段首尾重播而非无缝循环 |
| [requirements-pymunk.txt](../ui/shazhu/requirements-pymunk.txt) | 新预览专用锁定依赖；安装到 `_temp/shazhu/vendor`，不改主应用依赖或 APK |
| [verify_pymunk_preview.py](../ui/shazhu/verify_pymunk_preview.py) | 只核对新轮子预览的源码身份、真实碰撞记录、源沙落地、粒数、轨迹边界与画面非空；读取已有实算结果，不启动主应用 |
| [fine_grains.py](../ui/shazhu/fine_grains.py) | 01 细密沙粒：独立绘制、导出循环并打开预览；`--export-only` 不打开窗口 |
| [faceted_grains.py](../ui/shazhu/faceted_grains.py) | 02 棱角沙粒：不规则碎粒与细小明暗变化 |
| [small_clusters.py](../ui/shazhu/small_clusters.py) | 03 轻微聚团：一部分沙粒形成小簇，不用整柱透明度遮罩制造大洞 |
| [short_trails.py](../ui/shazhu/short_trails.py) | 04 短拖尾沙粒：长度有限的细碎运动痕迹 |
| [worley_grains.py](../ui/shazhu/worley_grains.py) | 05 胞元沙粒：Voronoi 单粒轮廓跟随共用颗粒轨迹，不再用与落点无关的整柱纹理场 |
| [sand_motion.py](../ui/shazhu/sand_motion.py) | 预览共用的非等距局部供沙、小团速度差、连续轨迹和逐粒落点；飞溅与滚动继承实际落地颗粒，不是真实碰撞或数学混沌求解器 |
| [verify_preview.py](../ui/shazhu/verify_preview.py) | 仅检查预览的供沙连续、上游继承、逐粒碰撞与落点关联、瓶颈衔接、局部密度及循环；不启动主应用，不是应用回归或帧率测试 |
| [preview_common.py](../ui/shazhu/preview_common.py) | 五方案共用的上游小团向下传递、颗粒轮廓、瓶颈衔接、实际落点飞溅与滚动及离线导出；无分格裁切、整柱摆动或强制向下变粗，只是外观草案，不导入主应用，目录排除 APK |
| [render_all.py](../ui/shazhu/render_all.py) | 一次生成五种 10 秒短片；`python ui/shazhu/render_all.py --export-only` 后台出片，不运行应用测试 |
| `_temp/shazhu/samples/` | 五种旧方案与 06 新轮子的 MP4、封面、细节图、播放器及生成信息；新轮子另有实算轨迹 NPZ 和碰撞记录，可再生，删除不影响应用 |
| `_temp/shazhu/vendor/` / `_temp/shazhu/pymunk*-probe/` | Pymunk 独立依赖与短片试算；可删除，不影响主应用 |
| [design/README.md](../design/README.md) | 设计与测试的分类入口 |
| [design/pages/hourglass.html](../design/pages/hourglass.html) | 按 MuMu 安卓截图记录初始/运行/暂停界面，区分截图事实、代码行为与未覆盖项 |
| `design/history/*.html` | 历史对照与候选方案，英文文件名保留版本号；历史结论不当作当前状态 |
| `design/images/` / `design/old-assets/` | 页面共用配图与停用素材，不进入 APK |
| `design/images/mumu-2.96-20261013/*.png` | 四张原始安卓截图的正式副本，供设计页引用；不因清理临时输出失效 |
| `design/images/mumu-2.96-20261013/details/*.png` | 设计文档的控件、玻璃、沙体、颈部、沙流与落点裁图；structure-marked.png 另加编号框，不是新运行证据 |
| `design/images/mumu-2.96-20261013/session-20261013-165114/` | GitHub 2.96 APK 真正安装后的同局阶段原图、暂停恢复原图及逐部位裁图；封面只用于视频定位 |
| `design/images/mumu-2.96-20261013/review-20261013/` | 用户指出的开局稀疏、出口接缝原图及已有录像连续抽帧；供 S04、NS03、S05 作为已知错误对照，不是正确基线 |
| `design/media/v2.96-20261013-165114/` | 完整原速录像、5 种循环短片、开局/末段片段与 manifest.json；记录 APK 摘要、运行代码、原始配置及采集时序 |
| `design/images/mumu-2.97-20261013/session-20261013-193739/` | 三项修复第三轮的真实阶段原图与连续裁图；前两轮试验归档暂存区 |
| `design/images/mumu-2.97-20261013/focused/` | 最新源码与原 APK 的同条件外观对照，以及 64 秒附近连续指定时刻裁图 |
| `design/images/mumu-2.98-20261013/` | 用户要求小幅调密后的四组同条件外观对照 |
| `design/images/mumu-3.0-20261013/` | 减缓下段变稀疏的三组同条件外观裁图 |
| [design/checks/records/v3.0/20261013.json](../design/checks/records/v3.0/20261013.json) / [下段疏密记录](../design/checks/records/v3.0/20261013.html) | 仅检查下段疏密调整，明确画布范围及底栏版本文字排布差异 |
| [design/checks/records/v3.1/20261014.json](../design/checks/records/v3.1/20261014.json) / [Pymunk 接入记录](../design/checks/records/v3.1/20261014.html) | 新碰撞方式的 PC 专项检查和阶段配图；未覆盖的旧规则矩阵继续标未测，不声称音频、帧率或 Android 验收 |
| [design/checks/records/v2.98/20261013.json](../design/checks/records/v2.98/20261013.json) / [调密记录](../design/checks/records/v2.98/20261013.html) | 仅针对疏密调整的外观观察与对照明细，不含帧率或全应用回归 |
| `design/media/v2.97-20261013-193739/` | 三项修复的完整/开局/暂停录像与出口、边缘原速循环；源码为 2.97，容器 APK 为 2.96 |
| [design/checks/README.md](../design/checks/README.md) / [index.html](../design/checks/index.html) | rules（规则）、records（版本记录）、notes（备注）的入口及资料生成方法 |
| [design/checks/style.css](../design/checks/style.css) | 检查资料共用样式，章节、局部图、操作与预期分别呈现 |
| [design/checks/rules/how-to-check.md](../design/checks/rules/how-to-check.md) | 执行环境、关联回归、发布拦截与当前覆盖缺口 |
| [design/checks/rules/checklist.json](../design/checks/rules/checklist.json) | 自动检查命令、完成标记、关联部件及人工必查项，统一入口读取 |
| [design/checks/rules/sand-flow-cases.json](../design/checks/rules/sand-flow-cases.json) | 9 组 36 个固定表现编号的唯一维护源：整体、局部、进度、操作、预期、配图、证据与关联项；新增 S04 首次落地疏密、NS03 出口接缝、S05 颗粒外缘 |
| [design/checks/rules/sand-flow.html](../design/checks/rules/sand-flow.html) | 从用例清单生成的带图规则，不是某版测试通过记录 |
| [design/checks/records/index.html](../design/checks/records/index.html) / [history.html](../design/checks/records/history.html) | 当前版本与旧版本的图示记录索引，旧结论不当作本轮复测 |
| [design/checks/records/v2.96/20261013.json](../design/checks/records/v2.96/20261013.json) / [图文记录](../design/checks/records/v2.96/20261013.html) | 已有开局/暂停片段按固定用例编号登记，所有用例均未验收 |
| [design/checks/records/v2.96/20261013-165114.json](../design/checks/records/v2.96/20261013-165114.json) / [安装版记录](../design/checks/records/v2.96/20261013-165114.html) | 2.96 APK 安装版素材补录，33 个编号各写实际现象、状态及证据；0 项通过，不与旧容器混用 |
| [design/checks/records/v2.96/20261013-review.json](../design/checks/records/v2.96/20261013-review.json) / [三项问题复核](../design/checks/records/v2.96/20261013-review.html) | 离线复核已有安装版录像与用户反馈；S04、NS03、S05 不通过，其余 33 项本轮未重新验收；旧采集及规则快照保留 |
| `design/checks/records/v2.96/evidence/` | 原部署/启动资料、12 项旧 PC 检查报告及日志副本、不可回填当前结果的规则快照 |
| [design/checks/records/v2.97/20261013.json](../design/checks/records/v2.97/20261013.json) / [修复图文记录](../design/checks/records/v2.97/20261013.html) | S04、NS03、S05 与暂停恢复的本配置初步观察，其余 32 项未验收；保留 8 项 MuMu 公式检查、部署摘要及规则快照 |
| [design/checks/notes/index.html](../design/checks/notes/index.html) / [记录格式](../design/checks/notes/record-template.md) | 版本变化、不能再犯的错、历史规则适用性与记录状态定义 |
| [design/checks/notes/qa-rules.md](../design/checks/notes/qa-rules.md) | 证据分级和量具纪律，唯一正式副本 |
| [design/checks/notes/old-criteria.md](../design/checks/notes/old-criteria.md) | 历史验收标定、红线与失败原因，唯一正式副本 |
| [design/checks/notes/old-acceptance.html](../design/checks/notes/old-acceptance.html) | 原 S/P 判据与历史图示，保留矛盾和过时结论并明确不作为当前规则 |
| `docs/` | [文档用途索引](README.md)；规则、验收、经验与决策历史 |
| `_temp/retired-tools/inventory.json` | 94 个旧工具的原位置、暂存位置、哈希和移出原因 |
| `_temp/retired-docs/` | 过期问答、交接、实施计划、旧代码快照，待用户确认删除 |
| `_temp/benchmark_logs/` | 本地测试输出和参照臂；删除不影响启动，但可能影响发布对照 |
| `_temp/regression-runs/` | 每次统一回归的日志、报告与人工记录模板；删除后须重新验证才可发布 |
| `_temp/pymunk-3.1/` | 新引擎的隐藏 PC 阶段图、专项报告和具名外观审阅；可再生，删除不影响应用 |
| `_temp/mumu-media/` / `_temp/mumu-20261013/` | 原始大录屏、旧容器试录、安装包、重装前 APK/私有目录/设置备份；不影响正式页面或应用启动 |

## 快速查找

- `src/tools/test_particle_continuity.py`：真实粒子的出生、持续轨迹、接触回收及实际 Mesh 顶点检查；含晚期 Mesh 创建失败的回退反例，不启动窗口。
- `src/tools/benchmark_particle_renderer.py`：固定状态重复测量 PC 更新、绘制及 GPU 完成的工作量，保留逐帧耗时、最慢 1% 和最慢帧；不等于实屏或 Android 帧率。
- `src/tools/check_particle_release.py`：历史3.7持续粒子的源码、隐藏运行和PC工作量证据检查；该版整体视觉通过已撤回，不是当前放行入口。
- `src/tools/test_particle_release.py`：持续粒子发布检查的正反例，拒绝缺帧、取消、身份错误和不完整性能证据。
- `src/tools/capture_stream_motion.py`：隐藏 PC 真实画布连续帧，记录逐帧堆面、尺寸、逻辑时间与源码身份。
- `src/tools/check_stream_motion_shape.py`：独立像素位移、宏观轮廓及呼吸检查；不使用着色器速度参数作为通过依据。
- `src/tools/test_stream_motion_shape.py`：速度方向、多采样、静态竖纹误匹配、矩形毛边等正反例。
- `src/tools/check_stream_wrap.py`：颗粒和轮廓相位循环边界的真实连续帧检查。
- `src/tools/check_motion_shape_release.py` / `src/tools/test_motion_shape_release.py`：3.6速度与形状分项发布检查及失败证据、源码身份的正反例。
- `design/checks/notes/stream-motion-shape-plan.html`：颗粒连续性与性能优先的最新约束、轮子选型备忘及投入限制；保留此前计划背景。
- `design/checks/records/v3.6/`：速度/形状原始帧、旧版反例和本轮完整过程录像。

- `src/app/short_grain_stream.py`：每颗真实存活粒子对应持续短粒图元；另提供共享触地散粒颜色，空中保留浅色、触近/触地转堆色，固定弱色差由不可变属性决定，普通/batch共用RGBA8；无沙量或计时写入。
- `src/tools/verify_short_grain_runtime.py`：隔离设置、原生隐藏 PC 画布的阶段与配色采集，记录实际绘制路径。
- `src/tools/record_short_grain.py`：按墙钟取真实画布帧、保留可变帧间隔，拒绝未刷新画布的录像。
- `src/tools/check_short_grain_visual.py`：检查柱内长缝、稀疏带和真实堆顶接触，不以单根线跨过某行视为密实。
- `src/tools/check_short_grain_release.py`：3.5 发布证据、源码身份、主沙粒与沙量及改动范围核对。
- `src/tools/test_short_grain.py` / `src/tools/test_short_grain_release.py`：绘制边界、比例、空沙和不完整/冻结录像的正反例。
- `design/checks/records/v3.5/`：3.4 失败对照、3.5 原始实图、全过程录像与具名审阅；不包含性能或音频验收。

- `src/tools/check_material_release.py`：重放已审阅 PC 材质对照，检查发布源码与实测源码一致，仅允许版本递增。
- `src/tools/test_material_release.py`：发布证据的正反例，包括漏帧、可见窗口和额外源码变化。
- `design/checks/records/v3.4/`：本次材质外观发布的原始 PC 证据压缩包、审阅记录及对比图。

```powershell
rg -n "飞溅|音频|颈部|版本" docs/file-index.md
rg --files src
```

旧探针需要恢复到原位置并重新核查才能运行。不能把“已归档”说成“已经验证无用”，
也不能把“文件还保留”说成“测试有效”。

- `design/checks/records/v3.7/`：持续粒子、真实像素可见性、完整录像、同条件 PC 工作耗时和源码证据；[本轮审阅](../design/checks/records/v3.7/review.html)。

- [3.7 对抗性复审](../design/checks/records/v3.7/adversarial-review.html)：记录实心杆与颗粒尺度等漏判，撤回整体外观通过结论，保留原测试证据。

- [沙流视觉修正方案与验收约定](../design/checks/notes/stream-visual-contract.html)：出口宽度、颗粒尺度、密度、轨迹及相邻连接的联合约束，分步小样与对抗性审核；方案阶段。

- [沙漏代码行为：类型、标准与期望](../design/checks/notes/code-behavior-types.html)：按真实代码定义容器/上沙/颈内/自由粒子/飞溅/下堆及阶段，记录1秒/5秒/50秒差异；待确认，不包含优化实施。

- [颈部粗细端点与周期函数候选](../design/checks/notes/neck-width/proposal.html)：最粗15%/最细3%的净宽候选、静态轮廓对照和幂曲线数值；含limits.png与proposal.json，未实施应用修改。

- [沙漏下一轮工作计划](../design/checks/notes/next-iteration-plan.html)：保留原计划及六步退出条件，追加3.8执行结果与正式记录；保留粗档、近满及暂停失败证据和复查范围。

当前沙流实现与检查入口：

- `src/app/neck_geometry.py`：按球内径定义净通道，1～5秒为15%，10小时及以上为3%，中间按对数时长参数作幂插值。
- `src/tools/release_decision.py`：当前审核决定及版本/源码身份；撤回和待验阻止发布，`--committed`核对决定及证据已纳入待推送HEAD。
- `src/tools/check_flow_release.py`：当前3.11发布入口，核对原图、连续帧、5秒/50秒实录、独立审核及PC工作量；对照3.10/c869e24，以精确源码边界保护主沙柱、质量、几何和计时，另与3.3/ff3b721核对完整飞溅发射。记录存在不等于当前外观通过。
- `src/tools/stream_review_metrics.py`：出口台阶、空层、实心杆及整体摆动反例量具。
- `src/tools/record_flow_review.py`：真正隐藏PC实时录像及暂停和末段记录。
- `src/tools/test_neck_geometry.py`：口径端点和尺寸/时长映射。
- `src/tools/test_stream_aperture.py`：实际图元出生边界及发射密度。
- `src/tools/test_receiving_surface.py`：低净空局部接收层与跨封顶连续性。
- `src/tools/test_release_decision.py`：当前决定及提交身份的发布反例，拒绝撤回、待验和未提交/未跟踪的批准证据。
- `src/tools/test_stream_review_metrics.py`：各类错误画面的独立反例。
- `src/tools/test_flow_release.py`：发布反例，包含当前精确源码边界、双随机状态及其他效果保护、实际像素暂停和近满接收；保留历史AST反例。
- `src/tools/test_splash_colours.py`：10项颜色反例，检查空中原色、触近/触地调色、固定粒子色差、浓淡端点、标量/向量与物理数组保持。
- `src/tools/test_splash_tint_batch.py`：13项tint batch反例，检查RGBA8打包、矩形完整覆盖、纹理绑定、分块/缩减/空帧清理和无颜色时的原绘制路径。
- `src/tools/test_splash33_impulse.py`：5项真实3.3发射对照，检查方法/参数AST、固定随机发射、完整初速、封顶和低净空；旧0.55/0.35作为负例。
- `src/tools/verify_splash_tint_paths.py`：实际普通Rectangle/tint batch路径对照；结果只覆盖真实执行的配置，不能代替完整动态审阅。
- `src/tools/test_landing_spread.py`：8项接触行为反例，核对实际坡面、埋藏不复活、暂停、标量/向量一致、原抛物线、后退表面及有限接触/停稳寿命。
- `src/tools/test_particle_material.py`：2项材料采样检查，核对固定种子/尺寸载荷及纹理像素偏移与实际图元几何一致。
- `src/tools/test_flare_occlusion.py`：5项实际闪光保留表达式检查，覆盖已埋闪光、接触、上缘、过期和坡面显露角。
- `src/tools/test_splash_occlusion.py`：4项实际飞溅绘制检查，覆盖普通/batch遮挡、显露坡角、空帧及无NumPy支路，并核对物理数组不变。
- `design/checks/current-review.json`：唯一当前审核决定，绑定候选版本、运行源码和阻挡项；历史passed不能覆盖它。
- `design/checks/rules/revision-cases.json`：本轮低分辨率关联场景。
- `design/checks/rules/revision-high-cases.json`：本轮高分辨率和极端场景。

- `src/tools/pixel_flow_review.py` / `src/tools/test_pixel_flow_review.py`：真实GL像素的局部亚像素下行位移及冻结/倒流反例，不等于逐像素身份或屏幕FPS。
- `src/tools/verify_receiving_pixels.py`：在隐藏PC固定近满几何及其他时钟，单独改变接收层相位并关闭该层作负对照，核对表面纹理实际像素变化；不以整个接收区的哈希变化代替局部运动证明。

3.8工程结果保留，整体质感续审未达；3.9包含细粒材质、闪光和飞溅遮挡修正。3.10的微粒明暗候选被独立拒绝，其贴坡与埋藏安全修复继续保留；交接亮圈则由用户反馈重新打开。
3.11按用户要求恢复真实3.3完整飞溅力度，空中原色和原发射量/粒径保持，仅对触近/触地散粒转堆色以消除亮圈；不回退主沙柱、颈宽、质量或计时，也不声称整套画面复刻3.3。
连续性20项包含材质缺失与Mesh失败分别回退的反例。历史飞溅黄金指纹用于存储等价，本次显式接触变化另用新反例和实际画面验收，不重存旧指纹。
5秒首次涨堆偏快仍为既有P2，不能据本轮结果声称全应用无问题。
本轮保留100%容量；范围为隐藏PC外观与相邻状态、用户已要求的PC单帧工作量，不包含Android、音频、真实屏幕FPS或完整应用验收。最终以当前审核决定及records/v3.11正式证据为准。
