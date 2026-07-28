# 04. API 契约基线

## 1. 通用约定

- 前缀：`/api/v1`。
- JSON 字段使用 `snake_case`；时间为 ISO 8601 UTC。
- 所有列表使用游标分页，禁止无上限返回。
- 所有写请求验证 CSRF/Origin（按认证实现选择）并记录安全审计。
- 长任务返回 `202 Accepted` + `task_id`，由任务端点或 SSE 查询状态。
- `POST` 长任务支持 `Idempotency-Key`。
- API 只返回用户有权访问的资源；禁止仅依赖前端隐藏。

### 1.1 错误格式

```json
{
  "error": {
    "code": "CONSENT_REQUIRED",
    "message": "需要先授权 DeepSeek 处理完整简历",
    "request_id": "req_...",
    "details": {"provider": "deepseek", "allowed_fallback": "deidentified"}
  }
}
```

错误码必须稳定、可测试；不能把供应商异常栈、提示词、密钥或正文返回前端。

## 2. 认证与邀请码

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/invites/validate` | 校验邀请码但不暴露剩余次数 |
| POST | `/auth/magic-links` | 发送邮箱免密链接；需年龄确认和邀请码 |
| POST | `/auth/magic-links/verify` | 消费单次 token，创建会话 |
| GET | `/auth/session` | 当前用户、角色、授权状态 |
| DELETE | `/auth/session` | 登出当前会话 |
| GET | `/auth/sessions` | 查看活跃会话 |
| DELETE | `/auth/sessions/{id}` | 撤销指定会话 |

`magic-links` 必须限流并返回统一响应，避免枚举邮箱。

## 3. 授权与隐私

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/consents/notices` | 当前隐私、服务商和分析告知版本 |
| GET | `/consents` | 用户已授予/撤回的授权 |
| POST | `/consents` | 对单一 provider + scope 主动授权 |
| DELETE | `/consents/{id}` | 撤回授权；阻止后续调用 |
| POST | `/privacy/exports` | 创建完整数据导出任务 |
| GET | `/privacy/exports/{id}` | 查询状态和限时下载链接 |
| POST | `/privacy/account-deletion` | 请求注销，进入 7 天恢复期 |
| DELETE | `/privacy/account-deletion` | 7 天内撤销注销 |
| DELETE | `/privacy/chat-history` | 单独删除聊天记录 |

`POST /consents` 不接受批量勾选多个供应商。完整简历与脱敏范围必须是不同 scope。

## 4. 简历与事实库

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/resumes/uploads` | 获取上传会话/预签名参数 |
| POST | `/resumes` | 上传完成确认并创建解析任务 |
| GET | `/resumes` | 列表，不返回正文 |
| GET | `/resumes/{id}` | 元数据、状态和授权需求 |
| DELETE | `/resumes/{id}` | 删除单份简历及派生数据，需确认 |
| GET | `/resumes/{id}/parse` | 解析状态和候选事实 |
| POST | `/resumes/{id}/facts/confirm` | 批量接受/编辑/拒绝候选事实 |
| GET | `/profile/facts` | 已确认事实库 |
| POST | `/profile/facts` | 用户主动新增事实 |
| PATCH | `/profile/facts/{id}` | 修改并生成新版本 |
| DELETE | `/profile/facts/{id}` | 废止事实，检查引用影响 |

上传限制：只允许白名单 MIME、扩展名和合理大小；服务端校验文件签名，执行恶意文件扫描和解压缩炸弹防护。

## 5. 求职方案与公司偏好

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/search-plans` | 列表/创建，最多 3 个 active |
| GET/PATCH | `/search-plans/{id}` | 详情/更新 |
| DELETE | `/search-plans/{id}` | 删除方案，不自动删除简历版本 |
| POST | `/search-plans/{id}/activate` | 激活并触发基础推荐刷新 |
| GET/PUT | `/search-plans/{id}/company-preferences` | 关注、优先、屏蔽 |
| POST | `/preferences/reset-learned` | 重置反馈学习偏好 |

薪资请求必须区分 `minimum_monthly_salary`、`target_monthly_salary` 和币种；第一版只接受 CNY。

## 6. 岗位与推荐

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/recommendations` | 按方案、日期、等级、状态筛选 |
| GET | `/recommendations/{id}` | 岗位、分项、证据、风险、来源 |
| POST | `/recommendations/{id}/feedback` | 感兴趣/不感兴趣 + 原因 |
| DELETE | `/recommendations/{id}/feedback` | 撤回反馈并重算学习偏好 |
| POST | `/jobs/import` | 用户提交岗位链接或职位描述 |
| GET | `/jobs/{id}/sources` | 去重后保留的全部来源链接 |
| POST | `/jobs/{id}/validity-check` | 低频请求重新检查有效性 |

推荐响应示例：

```json
{
  "id": "rec_...",
  "score": 82,
  "grade": "high",
  "hard_conditions": {"status": "passed", "items": []},
  "components": [
    {"name": "skills", "score": 86, "evidence": [{"fact_id": "...", "job_span": "..."}]}
  ],
  "risks": [{"code": "POSSIBLE_OUTSOURCING", "severity": "medium", "evidence": "..."}],
  "analysis_level": "deep",
  "source_links": []
}
```

## 7. 多 Agent 和 AI 助手

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/recommendations/{id}/deep-analysis` | 手动触发，校验每日额度 |
| GET | `/agent-runs/{id}` | 只返回总体状态和最终报告 |
| GET | `/agent-runs/{id}/events` | SSE：queued/analyzing/reviewing/completed/failed |
| POST | `/agent-runs/{id}/retry` | 仅重试可重试失败，保持幂等 |
| GET | `/assistant/threads` | 用户线程列表 |
| POST | `/assistant/threads` | 创建对话线程 |
| POST | `/assistant/threads/{id}/messages` | 消息与工具调用入口 |
| GET | `/assistant/threads/{id}/events` | 流式响应，不泄露内部推理 |
| POST | `/pending-actions/{id}/approve` | 用户批准工具动作 |
| POST | `/pending-actions/{id}/reject` | 拒绝并可提供反馈 |

Agent 工具必须使用显式白名单：搜索只读、读取用户自有事实、生成草稿、创建待确认动作。不得提供任意 SQL、任意 URL 抓取、文件系统或 shell 工具。

## 8. 简历版本和导出

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/resume-versions` | 按方案/岗位/类型筛选 |
| POST | `/recommendations/{id}/resume-drafts` | 创建岗位定制草稿，计入每日 3 次 |
| GET | `/resume-versions/{id}` | 内容、证据和修改差异 |
| PATCH | `/resume-versions/{id}` | 用户编辑草稿 |
| POST | `/resume-versions/{id}/confirm` | 确认最终内容 |
| POST | `/resume-versions/{id}/exports` | DOCX/PDF 异步导出 |
| GET | `/resume-exports/{id}` | 状态与限时下载链接 |

服务端必须重新验证事实引用。即使前端修改请求包含不存在的技能，也不能无提示写入“已确认事实”。

## 9. 通知

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/notifications` | 站内通知列表 |
| POST | `/notifications/{id}/read` | 标记已读 |
| POST | `/notifications/read-all` | 全部已读 |

通知只包含安全摘要和实体链接，不包含简历正文或敏感内容。

## 10. 管理员 API

统一前缀 `/admin`，必须二次鉴权或更严格会话策略：

- `/admin/invites`
- `/admin/users`（无简历正文）
- `/admin/sources`、`/admin/source-runs`
- `/admin/tasks`、`/admin/tasks/{id}/retry`
- `/admin/capabilities`
- `/admin/usage`、`/admin/budgets`
- `/admin/evaluation-runs`
- `/admin/feedback`
- `/admin/support-grants/{id}/access`

管理员不能直接创建用户事实、修改推荐分数或跳过授权。任何临时查看简历的请求必须验证有效 `support_access_grant`。

## 11. 限流和配额

- magic link：按邮箱哈希 + IP 桶限流。
- 模型任务：按用户、操作类型、供应商、全局预算四级校验。
- 多 Agent：每天自动 3 次 + 手动 3 次/账号。
- 定制简历：每天 3 个新版本/用户。
- 导出：限制并发和文件大小，防止资源滥用。

返回 `429` 时使用稳定错误码和 `retry_after`，不得偷偷继续调用供应商。

## 12. OpenAPI 与契约测试

- FastAPI OpenAPI 是可执行契约，不替代本文件中的业务约束。
- 前端类型由 OpenAPI 生成或持续校验。
- CI 检测破坏性契约变更。
- 每个错误码、权限分支和幂等场景必须有集成测试。
