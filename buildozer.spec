[app]

# 启动器中显示的可见名称
title = 跳跳的沙漏

# 内部包名(小写,无空格,无中文)
package.name = hourglass
package.domain = org.shalou

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf,wav,mp3
# 子目录里的非 .py 资源必须显式列出,否则不进 APK(字体不列会中文乱码)
source.include_patterns = sand_loop.wav,fonts/*.otf

version = 0.1.0

requirements = python3,kivy==2.3.0,pyjnius

# ⚠️ 关键:锁定 python-for-android 到 2024 年 tag。
# 不锁的话 2026 年的新版 p4a 默认下 Python 3.14 alpha,与 Kivy 2.3 C API 不兼容必失败。
# tag 名格式必须严格 v + 年.月补零.日补零,写错 git clone 会失败。
p4a.branch = v2024.01.21

orientation = portrait
fullscreen = 0

android.permissions =

android.api = 31
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
