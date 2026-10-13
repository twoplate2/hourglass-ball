# 文档索引

只保留代码本身无法替代的规则、验收依据与经验，不维护逐函数抄代码的说明。

| 文档 | 保留原因 |
|---|---|
| [沙流检查目录](../design/checks/index.html) | 图文规则、版本记录、备注三类入口 |
| [沙流用例](../design/checks/rules/sand-flow.html) | 上下沙、颈部、连接处、状态与跨配置检查，固定编号及带图说明 |
| [改完以后怎么检查](../design/checks/rules/how-to-check.md) | 必测清单、关联测试、发布拦截与未覆盖项 |
| [QA 纪律](../design/checks/notes/qa-rules.md) | 证据分级、正负对照、测量工具的可信性 |
| [历史验收依据](../design/checks/notes/old-criteria.md) | 用户判据、门槛标定和失败案例；旧数字不自动适用于当前版本 |
| [目录维护](structure.md) | 文件职责、输出位置、整理边界 |
| [逐文件索引](file-index.md) | 每个应用模块与工具的职责、使用限制和查找入口 |
| [踩坑经验](technical/lessons.md) | 排查方法与被事实推翻的判断 |
| [音频经验](technical/audio.md) | 无缝循环素材的加工取舍与实际踩坑 |
| [图标经验](technical/icon.md) | 视觉设计约束与素材制作经验 |
| [决策历史](archive/CLAUDE-history.md) | 历次实验、回滚和已知限制的溯源，不是当前规则 |

旧问答、交接、已实施的计划、代码快照及旧目录说明放在 `_temp/retired-docs/`，
不作为正式文档，由用户确认后删除。Git 中原有内容也可从历史恢复。

需要提供代码资料包时运行：

```powershell
python src/tools/_pack_source_md.py
```

输出 `docs/generated/source.md`，不进入 Git 或 APK；每次重新生成，不维护旧快照。
