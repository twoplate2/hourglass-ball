[app]

# 启动器中显示的可见名称
title = 跳跳的沙漏

# 内部包名(小写,无空格,无中文)
package.name = hourglass
package.domain = org.shalou

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf,wav,mp3
# 子目录里的非 .py 资源必须显式列出,否则不进 APK(字体不列会中文乱码,音效不列会无声)
source.include_patterns = sand_loop.wav,fonts/*.otf,sounds/*.wav,ui/*.png

version = 0.1.0

requirements = python3,kivy==2.3.0,pyjnius

# ⚠️ 关键:锁定 python-for-android 到 2024 年 tag。
# 不锁的话 2026 年的新版 p4a 默认下 Python 3.14 alpha,与 Kivy 2.3 C API 不兼容必失败。
# tag 名格式必须严格 v + 年.月补零.日补零,写错 git clone 会失败。
p4a.branch = v2024.01.21
# 构建后 hook: 往最终 manifest 注入 screenOrientation=fullSensor + resizeableActivity=true
# (buildozer 每个方向键只认 4 值, fullSensor 走 android.manifest.orientation + 此 hook 桥接)
p4a.hook = p4a/hook.py

# 四方向列表仅为过 buildozer 校验壳; 真正给系统的值由 android.manifest.orientation + hook 决定
orientation = portrait, portrait-reverse, landscape, landscape-reverse
# 直接写 manifest 的 screenOrientation=fullSensor(原生值, 不走 orientation 校验器)
android.manifest.orientation = fullSensor
fullscreen = 0

android.permissions =

android.api = 33
android.minapi = 21
android.ndk = 25b

android.archs = arm64-v8a,armeabi-v7a

android.allow_backup = True

icon.filename = %(source.dir)s/icon.png

# 启动屏背景必须和 app 背景同色,避免闪屏黑闪
android.presplash_color = #fdf6e3
presplash.filename = %(source.dir)s/presplash.png


[buildozer]

log_level = 2
warn_on_root = 1
