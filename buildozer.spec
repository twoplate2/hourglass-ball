[app]

# 启动器中显示的可见名称
title = 跳跳的沙漏

# 内部包名(小写,无空格,无中文)
package.name = hourglass
package.domain = org.shalou

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf,wav
# 子目录里的非 .py 资源必须显式列出,否则不进 APK(字体不列会中文乱码,音效不列会无声)
source.include_patterns = ui/fonts/*.otf,sounds/*.wav,sounds/voice/*.wav,ui/*.png
# 开发资料、原始素材和本地输出不进入 APK; gitignore 不控制本地打包。
source.exclude_dirs = docs,design,benchmark_logs,sounds/source,_temp,Temp,_kivyhome,__pycache__,src/__pycache__,src/app/__pycache__,src/tools,src/build,ui/shazhu

version.regex = APP_VERSION = ['"]([^'"]+)['"]
version.filename = %(source.dir)s/src/app/app_version.py

requirements = python3,kivy==2.3.0,pyjnius,numpy,pymunk==7.3.0,cffi==1.17.1
p4a.local_recipes = src/build/recipes

# ⚠️ 关键:锁定 python-for-android 到 2024 年 tag。
# 不锁的话 2026 年的新版 p4a 默认下 Python 3.14 alpha,与 Kivy 2.3 C API 不兼容必失败。
# tag 名格式必须严格 v + 年.月补零.日补零,写错 git clone 会失败。
p4a.branch = v2024.01.21
# 构建后 hook: 往最终 manifest 注入 screenOrientation=fullSensor + resizeableActivity=true
# (buildozer 每个方向键只认 4 值, fullSensor 走 android.manifest.orientation + 此 hook 桥接)
p4a.hook = src/tools/android_hook.py

# 四方向列表仅为过 buildozer 校验壳; 真正给系统的值由 android.manifest.orientation + hook 决定
orientation = portrait, portrait-reverse, landscape, landscape-reverse
# 直接写 manifest 的 screenOrientation=fullSensor(原生值, 不走 orientation 校验器)
android.manifest.orientation = fullSensor
fullscreen = 0

android.permissions =

android.api = 33
android.minapi = 21
android.ndk = 25b

android.archs = arm64-v8a

android.allow_backup = True

icon.filename = %(source.dir)s/ui/icon.png

# 用单色占位防止回退到框架默认图; 首帧后立即移除原生加载遮罩
android.presplash_color = #fdf6e3
presplash.filename = %(source.dir)s/ui/startup_blank.png


[buildozer]

log_level = 2
warn_on_root = 1
