# -*- coding: utf-8 -*-
"""
p4a 构建后 hook — 往最终 AndroidManifest.xml 注入横屏反旋转所需的两个声明。

背景(buildozer 1.5.0 的限制):
- buildozer.spec 的 `orientation` 键被 1.5.0 校验器锁死, 只认 4 值
  (landscape/portrait/portrait-reverse/landscape-reverse), 写不了原生的
  `fullSensor`/`sensorPortrait`。
- 想让系统判定 app 为"全方向现代 app"、在大屏(12L+/ZUI)横拿时给全屏窗口而非
  半屏 letterbox 盒, 必须在 manifest 里写 `screenOrientation=fullSensor` +
  `resizeableActivity=true` + targetSdk>=33。
- 所以 spec 里 `orientation` 四方向只是"过校验的壳", `android.manifest.orientation=fullSensor`
  走 buildozer 直写键, 本 hook 再在 before_apk_build 强制兜底(spec 四方向可能被 p4a 写成
  `unspecified`), 并叠加 resizeableActivity=true。

⚠️ 依赖 `org.kivy.android.PythonActivity` 的唯一类名锚点定位主 activity; 若 p4a 换类名或
   主 activity 写作自闭合 `<activity ... />`, 正则插入逻辑会破坏结构(见 _insert_attr 对
   自闭合的防护, 但仍建议留意)。hook 失败只 return 0 不抛错 —— 不阻断构建, 但横屏能力丢。
"""
import os
import re

# before_apk_build 把 buildozer 的 cwd 设为 dist 目录(内含生成后的 manifest)
_MANIFEST_CANDIDATES = ["src/main/AndroidManifest.xml", "AndroidManifest.xml"]

# 只匹配 `org.kivy.android.PythonActivity` 的 activity 开标签(非贪婪到首个 '>')
_ACTIVITY_RE = re.compile(r'<activity[^>]*org\.kivy\.android\.PythonActivity[^>]*?>')


def _find_manifest(ctx):
    """定位最终 manifest: 先测 cwd 下的两个常见路径, 再回退 ctx.distribution.dist_dir。"""
    for cand in _MANIFEST_CANDIDATES:
        if os.path.exists(cand):
            return cand
    try:
        dist_dir = ctx.distribution.dist_dir
        for cand in (os.path.join(dist_dir, c) for c in _MANIFEST_CANDIDATES):
            if os.path.exists(cand):
                return cand
    except Exception:
        pass
    return None


def _insert_attr(tag, attr):
    """在开标签尾部(自闭合 `/>` 或普通 `>`)前插入一个属性, 避免多出孤立的 `>`。"""
    i_close = tag.rfind('/>')
    if i_close != -1:
        # <activity ... /> → <activity ... attr />
        return tag[:i_close] + ' ' + attr + ' ' + tag[i_close:]
    i_close = tag.rfind('>')
    return tag[:i_close] + ' ' + attr + tag[i_close:]


def _inject_manifest(ctx):
    """找到 ==PythonActivity== 主 activity, 强置 screenOrientation=fullSensor +
    resizeableActivity=true; 有改动才回写。返回改动行数(0=无改动/失败, 不阻断构建)。"""
    path = _find_manifest(ctx)
    if not path:
        return 0
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    m = _ACTIVITY_RE.search(text)
    if not m:
        return 0
    tag = m.group(0)
    new_tag = tag

    # 1) screenOrientation=fullSensor(已有则替换, 没有则插入)
    if 'android:screenOrientation' in new_tag:
        new_tag = re.sub(r'android:screenOrientation="[^"]*"',
                         'android:screenOrientation="fullSensor"', new_tag)
    else:
        new_tag = _insert_attr(new_tag, 'android:screenOrientation="fullSensor"')

    # 2) resizeableActivity=true(31+ 默认 true, 显式写双保险)
    if 'android:resizeableActivity' in new_tag:
        new_tag = re.sub(r'android:resizeableActivity="[^"]*"',
                         'android:resizeableActivity="true"', new_tag)
    else:
        new_tag = _insert_attr(new_tag, 'android:resizeableActivity="true"')

    if new_tag == tag:
        return 0
    text = text[:m.start()] + new_tag + text[m.end():]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return 1


def before_apk_build(*args):
    """gradle 构建前注入 —— 生效点。args[0] 为 buildozer/p4a 传入的 ctx(带 distribution)。"""
    try:
        ctx = args[0] if args else None
        if ctx is not None:
            _inject_manifest(ctx)
    except Exception:
        # 绝不阻断构建; 失败 = 横屏能力丢失, 需另行排查
        pass


def after_apk_build(*args):
    """幂等兜底(此时 APK 已产出, 无实际作用, 仅保持一致)。"""
    before_apk_build(*args)
