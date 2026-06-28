# 跳跳的沙漏 — Android (Kivy) 打包工程

把 **pc 版球形沙漏**(`../hourglass_v2.py`，tkinter + PIL 真圆 + 完整球 + 球体积微积分)
重写为 Kivy，并用 **GitHub Actions 云端构建** 成 Android APK。全程不需要本地装
Android SDK / NDK / Buildozer，Windows 也能用。

> 本目录 `main.py` 是从 `pc/hourglass_v2.py` **从零重写** 的 Kivy 版，不复用 `../../android/main.py`。

## 桌面预览

```powershell
pip install kivy
cd E:\AI_Tools\other\shalou_claude\pc\apk
python main.py
```

非 Android 平台默认窗口 400×800(模拟手机竖屏)。点沙漏两个球 = 开始/暂停。

## 云端构建 APK

1. 本目录单独建一个 git 仓库并 push 到 GitHub(见下方"推送")
2. push 到 `main` 自动触发 `.github/workflows/build-apk.yml`，约 15-20 分钟产出 debug APK
   (后续命中 `~/.buildozer` 缓存约 5-8 分钟)；也可在 Actions 页手动 `workflow_dispatch`
3. 进 GitHub → Actions → 最近一次成功 run → 底部 **Artifacts** → 下载 `hourglass-apk.zip`
4. 解压得 `.apk`，传手机安装(需开"未知来源"权限)

### 推送(每个项目单独 repo)

```powershell
cd E:\AI_Tools\other\shalou_claude\pc\apk
git init -b main
git add .
git commit -m "球形沙漏 Kivy 版 Android 工程"
# 在 https://github.com/new 建空仓库(不勾 README/.gitignore/license)
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main
```

## 关键技术点(踩坑后的最稳组合)

```
Kivy 2.3.0 + python-for-android v2024.01.21 + buildozer 1.5.0 + cython<3.0 + host Python 3.10
```

- **`buildozer.spec` 里 `p4a.branch = v2024.01.21`** —— 没有这行必失败(2026 年新版 p4a
  默认下 Python 3.14 alpha，与 Kivy 2.3 的 C API 不兼容)
- **音效**：Android 用 `AudioTrack MODE_STATIC` + `setLoopPoints()` 硬件循环，声卡 DSP 自己回绕指针，绝对 0 缝隙，不受主线程卡顿影响；Windows 用 `winsound.SND_LOOP` 驱动层循环；其他桌面 fallback Kivy `SoundLoader`
- **渲染**：玻璃壳和沙体都用 Kivy 真圆 —— `Ellipse` 画球 + `Stencil` 裁出弓形沙面，
  复刻 pc"玻璃和沙必须同一种真圆技术、否则边缘失配"的核心原则，**不用 Mesh/多边形拼弓形**
- **中文字体**：`fonts/NotoSansSC-Medium.otf` + `LabelBase.register(name="Roboto", ...)`
  全局覆盖，否则装机后汉字全是豆腐块
- **弹窗配色**：`_SandBgPopup` 双层兜底（Popup 本体 `canvas.before` + 内部 `_container` `canvas.before`），覆盖 Kivy 默认深灰；按钮用暖金/沙色系独立配色（`POPUP_*` 常量），不随沙色变化

## 配色系统

弹窗和主界面底部按钮使用独立的暖金/沙色系，不受沙漏沙色切换影响：

| 常量 | 色值 | 用途 |
|---|---|---|
| `POPUP_BG` | `#faf5eb` | 弹窗底色 |
| `POPUP_GOLD_SEL` | `#caa450` | 选中按钮 / 确定按钮 |
| `POPUP_UNSEL_BASE` | `#a89078` | 未选基础周期按钮 |
| `POPUP_UNSEL_MULT` | `#b8a088` | 未选倍数按钮 |
| `POPUP_CANCEL_BG` | `#d8d2ca` | 取消按钮(暖灰) |
| `POPUP_TEXT` | `#332418` | 按钮/标签文字(深咖啡) |

主界面底部按钮：周期按钮 `#c4ae8e`(暖米色)，音效开 `#caa450` 92%(金色)，音效关 `#b7afa4`(暖灰)。

## 音效方案

`_SoundProxy` 按平台选最优方案：

| 平台 | 方案 | 循环方式 | 缝隙 |
|---|---|---|---|
| Android | `AudioTrack` MODE_STATIC | `setLoopPoints(0, frames, -1)` 硬件回绕 | 0ms |
| Windows | `winsound` | `SND_LOOP` 驱动层循环 | 0ms |
| 其他桌面 | Kivy `SoundLoader` | `loop=True` 应用层循环 | 可忽略 |

Android 方案的核心：手动解析 WAV 头提取 PCM 裸数据 → 一次性写入 AudioTrack 硬件缓冲区 → `setLoopPoints` 告诉音频 DSP 自动回绕读指针。整个循环发生在音频芯片内部，**不经过任何软件层**。pyjnius 传 `byte[]` 需 `jarray('b')(pcm)` 显式转换。

## 文件结构

```
apk/
├── main.py                       # Kivy 应用入口(从 pc/hourglass_v2.py 重写，~1230 行)
├── buildozer.spec                # 构建配置(锁 p4a v2024.01.21)
├── icon.png                      # 启动器图标 1024×1024
├── presplash.png                 # 启动屏 1080×1920(#fdf6e3 底)
├── sand_loop.wav                 # 15s 无缝循环 PCM 16bit mono 22050Hz
├── fonts/
│   └── NotoSansSC-Medium.otf     # 中文字体(~8MB, Apache 2.0 可分发)
├── .github/workflows/build-apk.yml
├── .gitignore
└── README.md
```

完整踩坑指南见 `../../android/BUILD_APK.md`，pc 版设计见 `../readme.md` 与 `../CLAUDE.md`。
