# 阶段 6：DeepSeek、LangGraph 多 Agent 与 AI 助手

前置：阶段5基础匹配已经可靠。阅读 `docs/07-ai-matching-agents.md` 全文、`docs/08-security-privacy.md` 和 `docs/10-cost-operations.md`。

## 目标

实现 DeepSeek 主模型、Qwen 授权备用、结构化 Model Gateway、LangGraph 多 Agent 深度分析、AI 助手白名单工具和人工确认断点。

## 必须交付

- 统一 Model Gateway：provider/scope 授权、JSON Schema、超时、token、有限重试、费用、熔断、安全日志。
- DeepSeek 主路径；Qwen 备用前重新检查独立授权；供应商 SDK 不进入业务模块。
- 模型分级配置和 allowlist；不得写死不可追踪的 latest。
- 匹配、风险、简历优化、学习资源、质量审查、协调器角色。
- 并行只读分析、最多一次结构修复、最终结构化报告。
- 每账号每日自动3次 + 手动3次；自动方案优先级；不活跃停止自动任务。
- 异步状态只显示排队/分析/校验/完成/失败，不返回内部对话/思维链。
- AI 助手只读查询、草稿和 `pending_action`；写事实/最终版本/删除必须批准。
- 预算80/90/100%降级和熔断。
- 模型不可用时规则 + 向量结果仍可用。

## 安全测试

- 岗位文本 Prompt injection 不能改变工具权限或泄露提示词。
- 未授权完整简历不得发送 DeepSeek/Qwen。
- Qwen 未授权时不能作为静默 fallback。
- 任意 URL、SQL、shell、跨用户资源调用被拒绝。
- 缺失技能和数字不能写入事实；质量审查阻断虚构。
- Schema 失败最多修复一次；失败状态不伪装完成。
- 日志/追踪/错误不含正文或完整响应。

没有真实密钥时完成 Adapter、contract test 和合成 E2E，但能力标记 `not_verified`；不要索要用户在聊天粘贴密钥。
