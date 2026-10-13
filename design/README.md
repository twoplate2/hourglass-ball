# 设计与测试入口

| 目录 | 放什么 |
|---|---|
| [pages/（设计稿）](pages/hourglass.html) | 根据安卓截图编写的当前界面与行为说明；未覆盖部分单独注明 |
| [history/（改动记录）](history/plan.html) | 历史版本对照、误诊记录和候选方案，不代表当前状态 |
| `images/`（配图） | 正式文档使用的安卓截图与历史配图；不依赖可清理的临时输出 |
| `media/`（录像） | 各版本完整录像、原速循环短片及采集身份记录；原始录屏备份在 _temp，页面用的资料正式保留 |
| [checks/（检查资料）](checks/index.html) | 分为 rules（规则）、records（版本记录）、notes（备注），每组用例有图示与固定编号 |
| `old-assets/`（历史素材） | 已停用的启动图，不进入应用 |

本目录下所有目录和文件名使用英文，正文保留中文；移动时同步修正相对链接和规则入口。
当前设计文档是 `pages/hourglass.html`，图文用例页是 `checks/rules/sand-flow.html`，
机器检查清单是 `checks/rules/checklist.json`。

历史页里的设备操作和“已修复”结论只用于追溯，不是当前验证授权或通过证明。
当前不在用户本机桌面启动 PC 测试；运行验证使用用户预留的 root MuMu，PC 检查交给 CI。
测试脚本在 `src/tools/`，原始输出在 `_temp/`；正式保留的版本记录与选定证据放入
`checks/records/`，不混进设计稿。
