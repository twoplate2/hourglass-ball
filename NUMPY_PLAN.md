# numpy 化粒子管线（1.3 目标）

用户决策（2026-10-02）：**走 numpy 路线**；**像素闸门必须 0 差异**（单 Mesh 那 26/255
散点差异因此出局）。

## 为什么是 numpy

MuMu 实测帧预算（2525 颗粒，15s 档）：

| 项 | 耗时 | 说明 |
|---|---|---|
| `update_particles` | 5.79ms | 每颗粒 ~2.3µs：9 次 dict 读 + 3 次 dict 写 + 1 次 pop + 3 个超越函数 |
| 粒子 redraw（grouping+打包+上传） | 3.53ms | `_group_stream_particles` 一趟 + 逐颗粒 pack |
| 粒子 GL canvas（24 个 Mesh） | 3.12ms | 24 次 draw + 24 次纹理上传 |
| 非粒子 | 3.91ms | 沙弓形/颈部/遮罩；**缓存方案已实测否决（只值 0.5ms）** |

把粒子相关的 6.65ms 全砍光帧也只有 9.7ms（+68%）——**200% 必须同时打掉 5.79ms 的物理**。
物理已经是「提局部变量 + 循环体内无方法调用」的形态，纯 Python 再榨最多 2×。

**答案：瓶颈是 CPython 字节码，不是 GPU**（粒子全关仍有 8.61ms）。强手机 GPU 再强也帮不上。

## 已验证的前提

本机 numpy 2.2.2，20 万随机样本，与 CPython 逐位对比：

```
x**0.5  0 ULP 差    sqrt  0 ULP 差    sin  0 ULP 差    乘加  0 ULP 差
```

⇒ 用 numpy 重写**不会改变数值**。⚠️ 这是 Windows/x86 的结论；aarch64 上 `np.sin` 若走
SIMD 实现可能与 libm 差 1ULP（亚像素，肉眼不可见），设备上需目视确认一次。

## ⚠️ 逐位等价的三条硬规则

1. **结合律必须照抄**。Python 的 `y += vy*dt + 0.5*g*dt*dt` 是
   `y + ((vy*dt) + (((0.5*g)*dt)*dt))`。numpy 里写 `py + pvy*pdt + 0.5*g*pdt*pdt`
   会算成 `(py + (pvy*pdt)) + (...)` —— **差一个 ULP**。必须显式加括号：
   `py = py + (pvy * pdt + 0.5 * g * pdt * pdt)`
2. **随机数全部留在 Python**，且调用顺序逐字不变。命中事件按**下标升序**回放：
   `if rand() < 0.25` → `if rand() < 0.50` → `uniform ×2` → `choice`，条件与顺序照抄。
3. **NaN/inf 不参与**：数组永远是满的（尾部是上一轮的残留值），所有 numpy 运算必须
   只作用于 `[0:pn]` 切片，不能整数组算 —— 否则残留的 NaN 会污染 or 产生 warning。

## 数据布局（`HourglassWidget` 上的并行 numpy 数组）

```
pn    存活粒子数（int）
px    x（屏幕坐标，每帧重算）
py    y
pvy   vy
pxo   x_offset      ┐ spawn 后不变
pwp   wobble_phase  │
pwa   wobble_amp    │
psz   size (1/2)    │
ptl   trail_time    │
pli   is_light (0/1)┘
pdt   step_dt（仅当帧新生的有偏值；每帧末尾统一置 dt）
```

容量按 2 的幂增长（`_p_grow(n)`），`pn` 之外的内容无意义。

**splashes / dust / flares 本轮不动**（仍是 dict 列表）——它们数量少一个量级，
且 `update_particles` 里的 splash 循环要单独验证。先吃最大的两块。

## 消费方清单（漏一个就静默出错）

`main.py`：
- `716 / 981` reset → `pn = 0`
- `1182` spawn → 写数组
- `1216` 物理主循环 → 向量化
- `1310` `self.particles = new_list` → 用布尔掩码压缩
- `1619 _group_stream_particles` → 返回**每个桶的下标数组**（不是 dict 列表）
- `1683 _draw_neck_grains` → 两趟循环改下标
- `1496 _reserve_stream_lines` → 不读粒子，**不用改**

`tools/flow_texture_experiment.py`（**出货渲染器**，`FLOW_RENDERER="texture"` 且仅 Android 装载）：
- `update(particles, ...)` → 改成收下标数组，用 numpy 一次算完 float32 字节
- `draw_texture_batches` → 去掉 `_group_stream_particles` 的双重调用

`tools/flow_batch_experiment.py`（无顶点纹理采样时的兜底 Line 渲染器）：
- `draw_batches` 的 `else` 分支 → 改下标

## 迁移顺序（每步跑一次 `tools/inspect_flow.py` 像素闸门）

参考图：`benchmark_logs/flow_visual_tex_pack2`；比对用 `--texture-flow` 那套。
**注意：两侧必须在同一状态下重生成再比**，不要拿隔了很久的历史目录当基准（本次踩过：
历史 label 之间的 40 万/323 万差异其实是中间态代码造成的，白查了两轮）。

1. **建数组 + spawn + reset**：数组成为真值，但**物理仍用旧的 dict 循环**——
   为此先写 `_p_sync_dicts()` 把数组同步回 dict 列表，保证 0 差异。
2. **物理向量化**：`update_particles` 的粒子循环改 numpy；命中事件回放留在 Python。
   此步做完删掉 `_p_sync_dicts()` 的一半（写回方向）。
3. **`_group_stream_particles` 返下标数组** + 改两个渲染器。
4. **打包向量化**：`float32 字节 = arr.astype('<f4').view(np.uint8)`，按桶 gather。

## 预期收益

| 步 | 省 | 累计帧 |
|---|---|---|
| 2 | 物理 5.79→~0.3 | 16.35→10.9 |
| 3+4 | grouping+打包 3.53→~0.4 | →7.75（**+111%**） |

GL 那 3.12ms 本轮不动（24→2 Mesh 已验证是 26/255 散点差异，与 0 差异红线冲突）。

## 外部依赖风险（CI 正在验）

`requirements` 加 `numpy` 后需 p4a recipe 在锁死的 `p4a.branch = v2024.01.21` 下编过
arm64/armv7。**CI run 37014200552 首次挂在版本门禁（不是 numpy）**，已收窄
`if:` 到 main/master 后重跑。若 numpy recipe 编不过，本计划作废，退回纯 Python 精修（+40~70%）。
