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
