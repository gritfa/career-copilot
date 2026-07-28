# 阶段 2：认证、授权与隐私骨架

前置：阶段1已通过。阅读 `docs/03-data-model.md`、`docs/04-api.md`、`docs/08-security-privacy.md`。

## 目标

实现邀请码 + 邮箱 magic link、会话/RBAC、18岁确认、模型服务商独立授权、审计和账号注销状态机。

## 必须交付

- 邀请码哈希、次数、期限和停用。
- Mailpit 本地 magic link；token 哈希、短时、单次、防重放和邮箱枚举。
- 用户/管理员 RBAC 和对象级权限基础设施。
- 年满18岁确认，未确认不得上传数据。
- DeepSeek/Qwen 按 provider + scope 独立告知、授权、撤回和版本记录；默认不勾选。
- `ModelAuthorizationGuard`，供后续 Gateway 强制使用。
- 7天注销请求/撤回状态机，期间停止新处理。
- append-only 安全审计，敏感字段过滤测试。
- 法律/隐私页面的版本化占位内容，明确需上线前专业复核。

## 必测场景

- 邀请码并发超用、magic link 重放/过期/枚举/限流。
- 普通用户访问管理员接口和其他用户资源被拒绝。
- 撤回 DeepSeek 后完整数据调用 Guard 失败；Qwen 授权不被继承。
- 日志/审计不含邮箱明文、token 或请求正文。
- 注销 pending 状态拒绝新任务，7天内可恢复。

不要接入真实供应商调用；使用接口和合成测试证明授权门控。
