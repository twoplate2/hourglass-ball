# 交接文档(jiaojie.md)—— 沙漏项目近期改动与上下文

> 用途:给接手的 AI / 开发者快速同步上下文。本文件随 `pc/apk/` 代码维护。
> 最近更新:2026-08-27。对应的 apk 仓库:https://github.com/twoplate2/hourglass-ball

## 0. 项目拓扑(一分钟版)

双端 + 一个"唯一基准"心智模型:

| 位置 | 说明 |
|---|---|
| `pc/apk/` | **Android(Kivy)版,本目录**。独立 git 仓库,remote = `github.com/twoplate2/hourglass-ball`,push `main` 自动触发 Actions 构建 APK |
| `pc/hourglass_v4.py` | **PC(tkinter+PIL)版,视觉/物理唯一基准**("铁律")。⚠️ **不在任何 git 仓库里**(`pc/` 未初始化 git),改动只存本地 |
| `pc/CLAUDE.md` `pc/readme.md` | 两版的架构文档与经验教训库,与本目录文档需同拍 |
| `MP3/`、`.gitignore` | 实录音效源文件只留本机,**已 gitignore 不入库/不进 APK** |

沙漏本质是"假物理":唯一真值 = `elapsed/duration`,所有可见几何由它推导。改物理/视觉前先对照 `../hourglass_v4.py`,不要用直觉替代已验证公式(历史上 Excel 式"我觉得更好"的改动全部回退)。

## 1. 这几天完成的改动总览

### 1.1 周期设置(两版同步)

- **档位**:`BASE_PERIODS` = 1秒 / 10秒 / 1分钟 / 10分钟("1 小时"已移除,新增"1 秒")。`MULTIPLIERS` 按钮仍是 `[1,2,3,5,10,20,30,50,70,100]` 两行。
- **对数倍率滑杆**(按钮下方):单段对数 **`⌈600^t⌉` 向上取整**,1–600 倍(t=1/2→25,t=1→600)。控件:apk = Kivy 原生 `Slider(min=0,max=1)` + `value_track=True, value_track_color=#caa450` + 未滑段暖灰棕贴图 `ui/slider_track.png`(#8a7a68,默认浅灰贴图太接近弹窗底色),保留**默认大滑杆样式**(曾自绘 `_SandSlider` 迭代两版后被用户否决,已删除;若再提"样式难看",先确认是颜色还是本体质感,不要重新自绘)。v4 = `tk.Scale`(0..1000 映射 t),`troughcolor="#8a7a68"`。
- **与倍数按钮并存但不同步**:两套输入都写同一个 `state['mult']`,预览只反映最后一次操作;互不改对方控件(按钮高亮/滑杆位置)。
- **时长上限**:两版 `MAX_DURATION = 360000` 秒(=100 小时;经历的中间值 600000 已回退)。`_closest_base_and_mult` 倍数上限随动 =600。
- **显示修复**:`_fmt_duration` 分钟/小时非整数保留 1 位小数(100 秒 → "1.7 分钟",不再显示"2 分钟");`_fmt_countdown_pair` 主界面倒计时按总时长自适应 `H:MM:SS / M:SS / 秒`。

### 1.2 音效系统(两版同步)

- **「音效:开*/关」开关已删**。**主界面音效按钮显示当前音效名**（沙沙声/水流声/风声/钟表声/无声音，静音时暖灰样式不变）。弹窗 = `SOUND_OPTIONS` 5 项，**点击即切换但弹窗不关**（高亮跟随、可连续试听），底部**「确定」按钮**（不加取消）是唯一关闭出口，运行中切换停旧播新。
- 静音实现:选「无声音」→ `_sound=None`(不建 proxy),`_play_sound/_stop_sound` 对 None 空操作;按钮切回旧样式:「音效:关」文案 + 暖灰底 `#b7afa4`(apk);有声时金色 + 「音效」。
- **素材**:沙沙声 = 合成 `sand_loop.wav`(保留)。水/风/钟表 = **实录**,由 `tools/import_sounds.py` 加工(miniaudio 解码 → 44.1kHz mono → 首尾最像片段选段 + 互相关对齐 + crossfade 无缝循环 → 幂律软压缩 + RMS 对齐沙沙声)。产物同时写 `apk/sounds/` 与 `../`(字节一致)。水/风 15s,钟表源仅 9s → **7s 短循环**(脚本已支持短源头整段成环)。历史:曾加过雨/海浪/篝火,用户要求移除;源 MP3 仍在 `MP3/`,要加回来改 `TARGETS` 重跑即可。
- 切换机制 = **重建 `_SoundProxy` 实例**(①新建失败→旧态原样 ②stop 旧 ③`close()` 旧(新增方法,AudioTrack `release()`)④挂新 ⑤running 续播)。配置键 `sound_on` → **`sound_name`**(两版值域逐字一致,旧键忽略)。
- `buildozer.spec` `source.include_patterns` 含 `sounds/*.wav`;workflow artifact 保留期 30→**90 天**。

### 1.3 代码级细节地图(apk/main.py,行号会漂移,以内容为准)

- 常/helpers:`_fmt_duration` / `MULT_SLIDER_MAX` / `MAX_DURATION` / `_mult_from_slider` / `_fmt_countdown_pair` / `SOUND_EFFECTS`+`SILENT_NAME`+`SOUND_OPTIONS`
- `_SoundProxy`(三平台 + `close()`);`_SandBgPopup`(浅色弹窗双层兜底)
- `HourglassWidget`:`_make_sound_proxy` / `_set_sound`(五步序,静音分支)/ `_rebuild_height_table` / `_raw_height_ratio`(守恒核心,勿动公式)
- `HourglassApp`:周期弹窗 `on_duration_picker`(滑杆行 + `×N` 标签 + `_on_slider_moved`)、音效弹窗 `on_sound_picker`(行切 3 个自动分行、`_on_sound_picked`、`_update_sound_btn`)、`_closest_base_and_mult`(tie-break:同 diff 取更小倍数)

### 1.4 球↔管曲线过渡(两版同步,2026-08-27)

用户报"接口圆柱有显示残留 / 像塞了个矩形垫块"。**根因不是管子,是球的肩**:球拿**极点**接管子,
球面在极点附近近乎水平 → 环壁在截口那行横向摊开 `√(R²−Ri²)=√(2R·ow−ow²)≈44px`(**与 neck_w 无关**),
撞上竖直管壁形成"扁平暗带 + ~84° 硬折角",无抗锯齿的 Kivy 上还带 5 行内急剧收敛的锯齿毛刺。

改成 **球壁 →(相切)二次贝塞尔 →(相切)短直筒**,轮廓全程 C1 连续:

- `_bezier2(p0,p1,p2,n)`:控制点 `P1 = 球在起点处的切线 × 管壁线` 的交点,起终点自动相切
- `self._taper = {out_pts, in_pts, t_out, t_in, y_bot}`,在 `_rebuild_height_table` 里一次算好
- **起点半宽必须 ≥ 肩台半宽**(最容易漏):按 `K×neck_w` 算在宽颈碰巧够、细颈只有 11px → 长周期肩台原样残留。
  正解 `w_out = min(R·0.45, max(t_out+2, TAPER_K·nw, shoulder·1.06))` → 恒 ~44px,**喇叭口固定、孔径随周期变**
- 颈管加高腾空间:v4 `_tube_h 23→40`(R 168→164);apk `tube_h = h*0.03→0.055`(R 自动缩 ~2.7%)。
  `y_bot` 有护栏不得越过 `neck_y`,否则上下过渡段重叠、直筒高度变负
- 渲染:擦掉曲线以外的球极冠 → 直筒 → 外轮廓(壁)+内轮廓(腔)。v4 用 PIL `polygon`(透明写入擦除);
  Kivy 无多边形图元 → 沿曲线**逐段 `Quad`**(需 `from kivy.graphics import Quad`)
- **沙柱直接复用 `in_pts`**(矩形 → 同形曲线带),与内腔天生贴合。两条后续修正(`_neck_sand_side`):
  ① 只填到**直筒下端**,下喇叭口敞开 —— 填满会变成"绿喇叭悬在空球上 + 一根细绳",沙流与颈部断开;
  ② `NECK_FILL=0.25s` 内**从上往下注满**,不用 `elapsed>0` 一帧切换 —— 喇叭口面积大,跳变刺眼
- 物理侧一行未动:`_raw_height_ratio`/守恒/粒子 shrink/wobble/重力全部原样;粒子 clamp 仍按孔壁,
  比新孔径更保守,冒烟测试 0 越界
- 参数:`TAPER_K=2.2` `TAPER_FILL=0.62` `TAPER_SEGS=10`(两版逐字一致)

## 2. 踩过的坑(接手必读,别重踩)

1. **wav 解码产物是 int16 刻度**:峰值限制/裁剪常数必须 `0.95×32767`(PEAK_LIMIT 语义),按 -1~1 算会把整段压成满幅方波(verify 曾经 RMS==peak)。
2. **慢涌流选段要加"接缝低谷惩罚"**:否则循环点落在涌浪低谷,每圈一个软音口(海浪虽已移除,经验通用)。
3. **纯合成噪声机械**("整数 bin + 正弦包络"方案已废弃):实录加工才对味;generate_sounds.py 已删除,现有 `tools/import_sounds.py`。
4. **Kivy lambda 闭包延迟绑定**:弹窗按钮/滑杆回调的 label/state/popup 一律默认参数或局部变量捕获(详情见文件内注释与历史注释)。
5. **弹窗高度自适应闭包必须捕获局部 popup**,不能引用 `self._sound_popup`(dismiss 后置 None,布局事件仍会触发闭包 → 选完即崩,已修)。
6. **winsound `SND_PURGE` 全局清场**:切音序必须"先停旧再播新";静音分支先 stop。
7. **AudioTrack `_active` 后置**于 play() 成功;`close()` 只用于切音释放。
8. **两版值域/文案逐字一致**(共享 `~/.hourglass_config.json`):改 SOUND_EFFECTS 名字或档位必须两版一起改。
9. **坐标翻转**:pc y 向下、Kivy y 向上;移植任何公式先镜像再看符号。
10. **`_closest_base_and_mult` 用 Python `round`(银行家舍入)**:写测试期望值时避开 .5 边界(曾因此测试假失败)。
11. **buildozer 约束铁律**:`p4a.branch = v2024.01.21`、cython<3.0、buildozer==1.5.0、host Py3.10、Java 17、ubuntu-22.04;禁 Python 3.14。
12. **资源全须显式列 include_patterns**:漏 wav = 桌面有声装机无声。
13. **改颈部几何要清查所有硬编码 `neck_w` 的装饰**:"颈部高光"画在 `cx±(neck_w−1)`,孔壁收窄后两条线糊在
    暗色管壁上,又变成新的"残留"。排查手法:**按状态分组比对同一行像素** —— 只在漏完态出现的伪影,
    必然来自只在漏完态画的图元。
14. **测试脚本会污染用户配置**:v4 的 `set_duration` 内部调 `_save_config`,自动化测试会改写
    `~/.hourglass_config.json` 的周期/沙色。测完记得还原(本次已踩,已还原成 2 秒)。

## 3. 当前 git 状态(重要)

- `pc/apk/`:已推送至 `origin/main`,最近三个提交(本批工作):
  - `6b1dad7` feat: 音效库 5选1(实录)+ 周期基档加 1 秒
  - `51c0514` fix: 周期显示非整数加一位小数
  - `9f2c93c` feat: 对数倍率滑杆(1-600)+ 倒计时 H:M:S
  - (本次) fix: 球↔管贝塞尔曲线过渡,消除扁平肩台/硬折角(见 1.4)
- 工作区未跟踪:`_workflow_experts.js`(**过时残留**,描述已废弃的旧粒子/合成方案,勿采信,建议删除或归档)。
- `pc/`(hourglass_v4.py、三个实录 wav、CLAUDE/readme)**没有 git**,同内容改动无法随 push 同步——需要时单独建仓。
  改 v4 前请手动备份;本次改动前的版本存于 `pc/backup/hourglass_v4_pre_taper.py`。
- `MP3/` 已 gitignore。提交者身份是自动生成的 `unknown <liangpan@cn.net.ntes>`(用户尚未配置 git user.name/email)。
- gh CLI 未登录(api 匿名还会 429)。

## 4. 接手后的快速验证

```powershell
python main.py                  # 从 pc/apk/:桌面预览(400×800)
python ../hourglass_v4.py       # PC 版
python tools/import_sounds.py   # 实录→wav 重生成(改 TARGETS/增益后)
python tools/import_sounds.py --verify
```

人工/装机重点(桌面看不了的部分):APK 音效循环无缝、滑杆对数手感与金色进度条观感、运行中切音、倒计时长时长格式、三星设备重播。

## 5. 未决事项 / 可选后续

- 滑杆视觉(默认大滑杆 + 金色 value_track)与四音听感已按用户要求收敛,但装包前仍以用户实测为准。
- 可加 **Release-on-tag workflow**(APK 进 Releases 不过期,解决"Actions artifact 过期后看不见"的老问题)。
- 旧的 android 版仓库 `twoplate2/shalou` 仍在(公开),其 artifact 已过期——用户"看不到 APK"是过期不是删除。
