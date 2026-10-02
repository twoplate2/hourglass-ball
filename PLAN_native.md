# 原生加速计划(+200% 目标)

用户目标:**不要把性能只提高几个百分点;不达到 +200% 不停。**
现状(MuMu 实测,15s 档峰载段 2500~2900 颗粒子):

```
帧 13.4ms = physics 4.7 + redraw 4.0 + GL canvas 3.5 + 残余 0.6
                                      ↑ 这三块就是全部可压缩空间
```

要 +200%(帧 13.4 → ~4.5ms),必须三块一起动:

| 阶段 | 内容 | 预期 |
|---|---|---|
| ① | `update_particles`(物理)搬进 C | 4.7 → ~1.0ms |
| ② | `_group_stream_particles`(分组)+ 端点纹理打包搬进 C | ~4.0 → ~1.0ms |
| ③ | splashes / dust / flares 的几百个 Rectangle 合成 Mesh | canvas 3.5 → ~2.0ms |

(③ 的 `_QuadBatch` 类**已经写进 main.py**但还没接线 —— 下一步就是接线。)

## 本地编译器(已解决)

本机没有 MSVC。用 pip 装的 zig 当 C 编译器,**已验证可用**:

```bash
pip install ziglang
INC=$(python -c "import sysconfig;print(sysconfig.get_paths()['include'])")
BASE=$(python -c "import sysconfig;print(sysconfig.get_config_var('installed_base'))")
python -m ziglang cc -O2 -shared -o flowcore.pyd flowcore.c -I"$INC" -L"$BASE/libs" -lpython311
python -c "import flowcore; ..."   # 能 import 即成功(实测 ping 返回 42)
```

⚠️ 本地编出来的是 **Windows x86_64** 的 .pyd,只用于**在 PC 上过像素一致性闸门**;
设备上要的是 aarch64/armv7 的 .so,由 CI 的 p4a recipe 编(见下)。

## 硬约束:逐位等价

`tools/inspect_flow.py` 的 `random.seed(23)` 下 104 张截图必须 **0 像素差异**。
- 浮点运算顺序、分支顺序、遍历顺序一律照抄 Python 版(用 C `double`)。
- **随机数留在 Python**:C 里凡是要 `random.random/uniform/choice` 的地方,
  通过缓存的 bound method 回调 Python,**调用顺序与原来逐字一致**(否则随机流错位)。
- `math.sin/cos/sqrt/exp` 等用 libm;桌面/设备 libm 可能有 1ULP 差 → 设备上要另做目视确认。

## 落地方式

1. `flowcore.c`(仓库根,或 `native/`):实现 ① ②,失败/缺失时 Python 原路照跑(fallback)。
   - 装载:`try: import flowcore except ImportError: flowcore = None`,每个热点函数
     `if flowcore: flowcore.xxx(...) else: 原 Python 实现`。
2. 本地:`tools/build_native.py`(或 README 里的 zig 命令)编 .pyd → 跑像素闸门。
3. 设备:p4a 本地 recipe(`p4a.local_recipes` **必须给绝对路径**,配方里
   `should_build` 要返回 True 否则缓存命中后永不重编;`requirements` 加模块名;
   workflow cache key 纳入 recipe 目录)。APK 里是 **Python 3.11.5**。
4. 每一步都先过像素闸门再上设备量帧率。

## 阶段③ 已试两轮, 未通过, 已回滚(2026-10-02)

把 splashes/dust 的逐条 `Rectangle` 换成单个 `Mesh` 的尝试**两次都没过像素闸门**,
已 `git checkout -- main.py` 回滚, 仓库保持在已验证的 1.2。

已知结论(下次直接接着做):
- `Mesh.vertices` **必须给 `array("f")` 之类 buffer**, 给 Python `list` 会导致方块
  **完全不渲染**(第一轮就是踩了这个, 表现为颈部飞溅方块整片消失)。
- 裸 `Mesh` 走 Kivy 默认 shader 会乘一次纹理采样; 没绑纹理时方块不可见 ——
  要放进 `RenderContext` + 极简 shader(`frag_color = color * vec4(1,1,1,opacity)`,
  `gl_Position = projection*modelview*vec4(vPosition,0,1)`), 与沙流批处理同一套路。
- **补零尾部算错也会错**: 每个方块是 4 顶点 x 4 float = **16** 个 float, 早先按 8 补,
  顶点数组短一半 -> 末尾方块全部错位, 表现为跑到画面左边缘(x=6~34)的杂散几何。修成 16 后
  杂散几何消失, 但**飞溅方块仍然完全没画出来**(对照图: 左侧有浅色方块, 右侧整片没有)。
  下一步查: Mesh 的 fmt/stride 是否真被采纳(可打印 `mesh.vertices` 长度 与 capacity*16
  比对), 或干脆改用**沙流渲染器那套已验证的写法**(静态顶点数组 + 位置放纹理 + shader 里
  算偏移), 不要再用"裸 Mesh 直接写顶点"这条路。
- 即使改成 `array("f")`, 仍有 4 张图有真实差异(t≈1.0/2.0/15.0, 832~3392 像素,
  通道差最大 136), 说明**还有一处几何/顺序不同**没找到。下一步应把差异 bbox
  (164,372)-(252,448) 那块的"改前/改后"逐像素网格打出来比对(README 里的 ASCII 网格手法)。

## 当前未完成状态

- `main.py` 已加 `_QuadBatch`(batched mesh 版 splashes/dust),**尚未接线**,
  现在是无副作用的死代码 —— 要么接线(阶段③),要么删掉。
- 版本:1.2(已推)。下一版若推需要 +1(→ 1.3)。

---

# 关键判断:字典换成平铺数组才是 +200% 的前提

为什么"把 Python 循环照抄进 C"到不了 +200%:循环体里每个字段都是一次
`p["x"]` 字典查找, C 里同样要 `PyDict_GetItem`(哈希+引用计数), 只快 2~3 倍。
**必须先把粒子状态改成平铺的 `array('d')` 字段数组**(每个字段一条数组, 粒子用下标 i):

```
self.px, self.py, self.pvy, self.pxo, self.pwp, self.pwa, self.ptrail, self.psize, self.plight
self.count                      # 存活粒子数, 删除用"尾部交换"
```
这样 C 侧 `PyObject_GetBuffer` 拿到裸 double*, 逐粒子只有几次内存读 → 比现在快
**10~20 倍**, 才能把 physics 4.7ms → 0.3ms、packing 4.0ms → 0.5ms。

## 改造清单(按顺序做, 每步都过像素闸门)

1. 新增 `self.pa`(一个 `dict` of `array('d')`)与 `_spawn(...)` / `_kill(i)`(尾部交换);
   先把**写入端**改完(spawn 处 1182 行附近), 读端暂时用 `_sync_from_arrays()` 把数组
   写回 dict 让老代码继续跑 —— 这一步应当 0 像素差异。
2. 逐个把读端换成下标: `update_particles`(1157-1230)、`_group_stream_particles`、
   `TextureFlowBatch.update`、`_draw_neck_grains`、`_spawn_dust`、`_trail_len`、
   `_sand_half_w` 的调用处。每换一个跑一次闸门。
3. 全部换完后, 数组是唯一真值; 此时写 `flowcore.c`:
   - `flowcore.step(px,py,pvy,..., count, params, rng_cb)` → 原地更新, RNG 走回调;
   - `flowcore.pack(px,py,pvy,ptrail,psize, indices, data, top_limit, motion_scale)`
     → 直接写端点纹理字节。
   本地 `python -m ziglang cc -O2 -shared -o flowcore.pyd flowcore.c -I<inc> -L<libs> -lpython311`
   → 跑闸门 → 上 MuMu。
4. splashes/flares/dust 同样平铺(它们的 update 循环也在 update_particles 里);
   `_QuadBatch` 的 Mesh 版本一并修好(见上一节的两条结论)。

⚠️ 浮点必须用 C `double`, 表达式顺序与 Python 逐字一致; `random` 全部回调 Python。

## 实测:C 化"字典版"只有 2.2 倍(2026-10-02)

`native/flowcore.c` 的 `pack_stream`(与 Python 版**逐字节等价**, `tools/test_native_pack.py`
200 组随机数据全过)在 PC 上跑 3000 颗粒:

```
Python 0.503 ms/帧   ->   C 0.225 ms/帧   快 2.2 倍
```

**这就是"必须换平铺数组"的硬证据**:瓶颈不是解释器, 而是每颗粒 4 次
`PyDict_GetItemString`(每次都重新哈希键字符串)。换成内置 interned key 的
`PyDict_GetItem` 还能再快 1.5~2 倍, 但也到不了 10 倍 —— 只有把状态放进
`array('d')`、C 侧用 `PyObject_GetBuffer` 拿裸 `double*`, 才可能 10~20 倍。

已完成且可复用的: `native/flowcore.c` + `tools/build_native.py`(zig 本地编译) +
`tools/test_native_pack.py`(逐字节等价测试)。下一个 C 函数按同样方式加进去即可。

---

# 下一步:update_particles(4.7ms)进 C —— 设计与关键坑

代码位置: `main.py:1157` 起, 每颗粒主循环从 `1216` 行 `for p in self.particles:` 开始。
循环体是**纯算术**(y/vy 积分 → hit 判定 → shrink 流量守恒 → wobble → 横向 clamp),
只有两处要随机数: 触底时 `rand()<0.25` 生成 flare、`rand()<0.50` 生成 splash。

## ⚠️ 随机流必须逐字保持

Python 版是**逐颗粒交错**地抽随机数。若 C 里把事件攒到最后再处理, 随机数顺序就变了,
`random.seed(23)` 的逐像素验收会直接崩。做法: C 按颗粒顺序把命中事件写进一个小缓冲
(下标 + x/y/vy/speed), 返回给 Python; **Python 按同一顺序回放**这些判定与抽样
(flare 判定 → splash 判定 → rand_uniform ×2 → rand_choice), 顺序与原来完全相同。

## ⚠️ 只搬算术的收益有限 —— 这才是平铺数组真正不可省的地方

打包函数能到 12 倍, 是因为它**只读不写**字典(4 次读/颗粒)。
物理每颗粒要 **读 + 写回**(y/vy/x 各一次写), interned key 下每次约 40ns,
7~8 次字典操作 ≈ 0.3µs/颗粒 —— 相对现在 1.7µs 只有约 2~3 倍。
**要把物理拿到 10 倍, 必须把这几个字段改成平铺 `array('d')`**, C 侧用
`PyObject_GetBuffer` 拿裸 `double*` 原地改, 零字典操作。

所以顺序建议:
1. 先只把物理做 C 化(2~3 倍, 4.7ms → ~1.7ms, 峰值帧约 +23%), 过逐字节+逐像素闸门。
2. 再做 y/vy/x/x_offset/wobble_phase/wobble_amp/size/trail_time 的平铺数组改造
   (读端 `_group_stream_particles` / 打包 / 颈部颗粒 / dust 都要换下标),
   每换一处跑一次闸门; 换完物理进 C 可再快 3~5 倍。
3. canvas: splashes/dust 的 Mesh 合批(见前面的两条结论) + 可能的 flow 批次数合并。

## update_particles 逐字规格(已逐行核对 main.py:1216-1310)

每颗粒(顺序 = self.particles 顺序):

```
step_dt = p.pop("_step_dt", dt)          # 只有帧中新生的粒子有
y, vy = p["y"], p["vy"];  old_y, old_vy = y, vy
读 x_offset, wobble_phase, wobble_amp, size
y += vy*step_dt + 0.5*g*step_dt*step_dt
vy += g*step_dt
hit = (y <= mound_top)
若 hit:  d = old_y - mound_top;  distance = d>0?d:0;  v = -old_vy;  speed = v>0?v:0
        denom = speed + sqrt(speed*speed + 2*g_abs*distance)
        hit_dt = 2*distance / (denom>1e-6 ? denom : 1e-6);  hit_dt = min(hit_dt, step_dt)
        y = mound_top;  vy = old_vy + g*hit_dt
fd = gen_y - y;  fallen_dist = fd>0?fd:0
若 y > lower_cut:  shrink = 1.0
否则: below_tube = lower_cut - y
      v_at_y = (source_speed_sq + 2*g_abs*below_tube) ** 0.5
      target = (source_speed / v_at_y) ** 0.5;  target = target<=0.70 ? 0.70 : target
      shrink = below_tube < 40 ? 1.0 + (target-1.0)*(below_tube/40.0) : target
      dist_to_floor = y - mound_top
      若 0 < dist_to_floor < 30:  shrink *= 1 + (1 - dist_to_floor/30)*0.4
x = cx + x_offset*shrink + sin(fallen_dist*0.07 + wobble_phase)*wobble_amp*(1-shrink*0.4)
若 y >= lower_top:  lim = tube_lim
否则: dy = y - lower_center;  r = Ri2 - dy*dy;  raw_ball = r>0?sqrt(r):0
      t = min((lower_top - y)/30.0, 1.0);  lim = tube_lim + (raw_ball - tube_lim)*t
half_stroke = size>1 ? size : 0.5;  lim = max(lim - half_stroke, 0)
off = clamp(x-cx, ±lim);  x = cx + off
若 hit:  (mound_top > lower_bot+1) 时 peak_offset = peak_offset*0.97 + (x-cx)*0.03
        **该颗粒不再进入 new_list(落地即移除)**
否则: 写回 p["y"], p["vy"], p["x"], 并 append 到 new_list
```

C 侧接口建议:
`flowcore.step(particles, consts, dt)` -> 返回命中事件列表
- `consts` = (g, g_abs, gen_y, mound_top, lower_cut, lower_top, lower_center, Ri2,
  tube_lim, source_speed_sq, source_speed, cx, lower_bot, motion_scale, now)
- 返回: [(x, vy, step_dt, hit_dt), ...] **按颗粒顺序**; 以及新的 mound_peak_offset
  (EMA 只依赖 x, 可在 C 里算完回传)。
- Python 侧按返回顺序**逐个回放随机数**:
  `if rand() < 0.25: flare(...)` → `if rand() < 0.50:` → `bounce = min(110*motion_scale,
  max(-vy,0)*rand_uniform(0.14,0.28))` → `angle = rand_uniform(-0.85,0.85)` →
  `size = rand_choice([1,1,2])`; 然后 append splash。
  ⚠️ 三个随机数**只在 splash 成立时才抽**, 顺序与条件必须逐字照抄。
- 命中颗粒的移除: Python 侧按返回的命中下标过滤 `self.particles`。

**逐位等价的风险点**: C 用 `double`、表达式顺序照抄; `** 0.5` 用 `sqrt()`(与 CPython 的
`float.__pow__` 对 0.5 的路径一致, 但**要用 sqrt** —— `pow(x,0.5)` 在某些 libm 上差 1ULP);
`sin` 用 libm。这些是唯一可能不逐位一致的算子, 所以**必须过逐像素闸门**, 而且要在一台设备上
再目视确认一次。

## CI 真实报错(2026-10-02, run 69 诊断注解)

```
recipe 编译阶段: ModuleNotFoundError: No module named 'setuptools'
失败命令: pythonforandroid.toolchain create --dist_name=hourglass --bootstrap=sdl2
          --requirements=python3,kivy==2.3.0,pyjnius,flowcore
          --arch=arm64-v8a,armeabi-v7a --ignore-setup-py --debug
```

即: recipe **被找到了**、也走到了 `build_compiled_components` 调 `setup.py build_ext`,
但跑 setup.py 的那个解释器里没有 setuptools。recipe 里写的 `depends = ["setuptools"]`
是**目标侧**的 recipe, 并不等于把 setuptools 装进**构建期用的 hostpython 环境**。

候选修法(按尝试顺序):
1. recipe 里改用 p4a 的 hostpython 显式调用: `env = self.get_recipe('hostpython3',
   self.ctx).get_build_env(arch)` 再 `install_python_package`; 或在 recipe 里覆盖
   `build_compiled_components` 前先 `self.ctx.hostpython -m pip install setuptools`。
2. 干脆不走 setuptools: 在 recipe 里自己写 `build_arch`, 直接用 NDK 的 clang 编一个
   .so(`-shared -I<hostpython include> -I<python3 recipe include>`), 不碰 setup.py ——
   纯 C、没有依赖, 这条路最可控(参考 p4a 里 libffi 这类 C-only recipe)。
3. 换 `CythonRecipe` 骨架试试(同样是 setup.py 路线, 不一定解决)。

诊断通路(可复用): workflow 里失败时
`grep -i -E "error|traceback|exception|not exist|ValueError|recipe" buildozer.log | tail -14`
→ 用 `::error title=...::` 发成注解 → **公开 run 页面直接可读**(job 日志要管理员权限,
注解不要)。

---

# 不用 C 的路线:消融实测(2026-10-02, 1.2 为底)

同机同日、2525 颗粒、15s 档 2~6s 段均值:

| 变体 | physics | redraw | canvas | 帧 |
|---|---|---|---|---|
| 基线 | 5.79 | 4.98 | 4.50 | **16.35ms** |
| 关粒子批 | 4.93 | **1.45** | **1.38** | **8.61ms** |
| 关沙弓形 | 4.83 | 4.36 | 4.19 | 14.28ms |

- **粒子管线 = 7.7ms(占整帧 47%)**;physics 另 5.8ms ⇒ 与粒子相关的共 ~13.4ms(82%)。
- **"缓存静态大块"不值** —— 两个沙弓形合计只值 ~0.5ms(GPU 填大块本就便宜),
  而且 FBO 会丢 MSAA(改画质)。**这条路实测否决, 不要再试。**
- 下一步(不用 C 的最大可打点): **GL 侧 3.1ms**。现在按"11 档颜色 × 2 线宽"分成
  **24 个 Mesh**, 每帧 24 次状态切换 + 24 次纹理上传 + 24 次 draw call。做法:
  - 颜色改**顶点属性**(Kivy Mesh 支持自定义 fmt, 如 `(b'v_color',4,'float')` +
    shader 里 `attribute vec4 v_color;`), 线宽也可做成顶点属性(端帽偏移),
  - 所有粒子合成**一个 Mesh**、一个共享端点纹理: capacity 取 reserve 的 2 次幂
    (≈4096 → 纹理宽 3*4096=12288, MuMu 报 texture max size 32768, 安全),
  - 预计 24 次 → 1 次, 省 1.5~2ms(+10% 上下), 且**可做到逐像素相同**。
- 验证顺序照旧: 本地 `tools/inspect_flow.py` 像素闸门(0 差异) → 推 MuMu 量
  canvas/redraw 分项。
