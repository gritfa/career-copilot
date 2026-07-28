# 编码 Agent 分阶段提示词

使用方式：

1. 先把 [00-master.md](00-master.md) 作为总控上下文交给编码 Agent。
2. 每次只执行一个阶段提示词。
3. 上一阶段的验收命令、证据和 Git 状态确认后，再进入下一阶段。
4. 提示词中的“完成”只代表对应阶段，不得自动扩展到云部署或 Beta。

| 顺序 | 提示词 | 目标 |
|---:|---|---|
| 0 | [总控边界](00-master.md) | 全局需求、安全与证据规则 |
| 1 | [脚手架](01-scaffold.md) | monorepo、本地基础设施、CI |
| 2 | [认证与隐私](02-auth-privacy.md) | 邀请码、magic link、授权、审计 |
| 3 | [简历与事实库](03-resume-facts.md) | PDF/DOCX、解析、确认、版本 |
| 4 | [岗位来源](04-job-sources.md) | 方案、快照、连接器、标准化、去重 |
| 5 | [匹配与反馈](05-matching.md) | 硬规则、Embedding、评分、证据 |
| 6 | [多 Agent 与助手](06-agents.md) | DeepSeek、LangGraph、审批、降级 |
| 7 | [定制简历与导出](07-resume-export.md) | 编辑器、模板、DOCX/PDF |
| 8 | [后台与数据权利](08-admin-privacy-ops.md) | 管理后台、导出、删除、费用 |
| 9 | [最终验收](09-acceptance.md) | E2E、安全、60组评测、本地交接 |

如果编码 Agent 无法读取总控文件，必须先把 `00-master.md` 的全文一并粘贴；不能只给阶段标题。
