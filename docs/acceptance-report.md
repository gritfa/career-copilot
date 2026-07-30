# 阶段 9 验收报告：本地 MVP 端到端验收

> **阶段 10 说明（2026-07-30）**：本报告第 1～6 节为**历史记录**——全部实跑结果对应的代码
> 最终提交为 main `12be67e`（报告撰写时记作「`9649fc9` + 未提交的阶段 9 改动」，即后来提交
> 的 `12be67e`）。这些历史命令与数字未在阶段 10 重新执行，也不得被伪造成重新执行过。
> 本报告结论已按 ADR-002 **降级**，见新增第 7 节。

- 日期：2026-07-29
- 基线：main HEAD `9649fc9`（阶段 9 改动当时尚未提交，后合并提交为 `12be67e`，见第 6 节文件清单）
- Alembic head：`b3f7c1d9e6a2`
- 结论（2026-07-30 已降级，见第 7 节）：~~**「本地 MVP（合成模型）」端到端旅程验收通过**~~ →
  **「合成模型 + 合成/导入岗位的本地演示」通过**。按 docs/09 第 1 节证据层级，本报告最高只主张到**层级 4（本地端到端流程通过）**；层级 5～7（真实供应商 / 阿里云 / Beta）一律 `not_verified`，见第 3 节。**不宣称 Beta 已上线。**

---

## 1. 本地已验证（附命令与数字）

以下每条均为本机（macOS，PG 55432 / Redis 56379 / Mailpit 1025/8025 真实容器）实跑结果，命令可重复执行。

| # | 项目 | 命令 | 结果 |
|---|---|---|---|
| 1 | 后端 lint | `cd backend && uv run ruff check .` | All checks passed!（退出码 0） |
| 2 | 后端全量测试（真实 PG/pgvector + Redis + Mailpit，无 SQLite 冒充） | `cd backend && uv run pytest -q` | **217 passed** in 23.69s（退出码 0；阶段 1–8 基线无回归） |
| 3 | 端到端验收脚本（本报告核心） | `cd backend && uv run python scripts/acceptance.py` | **18/18 步 PASS**（退出码 0，全文见 1.1） |
| 4 | 前端生产构建 | `cd frontend && npm run build` | 退出码 0（全部路由构建成功） |
| 5 | 浏览器 E2E（Playwright + Chromium，真实 uvicorn:8901 + Next.js:3901 进程） | `cd frontend && npm run test:e2e` | **2 passed** (11.5s)：①邀请码+邮箱免密登录→Mailpit 收信→verify→首次建档引导→模型授权页；②失效 magic link 失败路径 |
| 6 | 迁移从空库可重放 | 验收脚本第 2 步 / E2E globalSetup（每次运行都 DROP+CREATE+`alembic upgrade head`） | head=`b3f7c1d9e6a2`，重复执行通过 |

### 1.1 验收脚本实跑输出（2026-07-29，退出码 0）

拓扑说明（务实方案）：真实 PostgreSQL(pgvector)/Redis/Mailpit + httpx ASGI 进程内调用同一 FastAPI 应用工厂，Celery eager（同一任务代码同步执行），邮件走真实 SMTP→Mailpit。独立库 `careercopilot_acceptance` 每次重建，不污染 dev 库，可重复执行。Gate：任何断言失败即非零退出。

```text
==============================================================================
CareerCopilot 阶段 9 端到端验收（真实 PG/pgvector + Redis + Mailpit，Celery eager）
数据库: careercopilot_acceptance@localhost:55432  Redis: redis://localhost:56379/2
==============================================================================
[ 1/18] PASS 基础设施可达（PG/Redis/Mailpit） (0.04s)
        PostgreSQL:55432 / Redis(db2) / Mailpit 可达；Mailpit 已清空
[ 2/18] PASS 空库重建 + Alembic 迁移 head (0.96s)
        库 careercopilot_acceptance 已重建并迁移到 head=b3f7c1d9e6a2
[ 3/18] PASS CLI 创建邀请码 (0.24s)
        invite_id=397bb46a-... max_uses=5
[ 4/18] PASS 健康探针与能力端点（诚实标注） (0.03s)
        live/ready 通过；capabilities 13 项，模型类能力均为 not_verified
[ 5/18] PASS 邀请码 + 邮箱免密注册登录（Mailpit 真实收信） (0.07s)
        注册+免密登录闭环通过（Mailpit 真实收信）；user_id=381267ba-...
[ 6/18] PASS 模型授权告知与授权（默认不勾选） (0.01s)
        deepseek full_resume 授权成功（notice vdeepseek-2026-07-28.1，默认不勾选已验证）
[ 7/18] PASS 简历上传 → 解析 → 候选事实（受保护属性丢弃） (0.07s)
        PDF 上传→解析闭环通过；候选 8 条 / 类型 5 种；受保护属性丢弃 5 项且 0 条进入候选
[ 8/18] PASS 用户确认事实（接受/拒绝，仅确认项入库） (0.02s)
        确认 7 条 / 拒绝 1 条；事实库 7 条全部经用户确认
[ 9/18] PASS 创建求职方案 (0.02s)
        求职方案创建成功 plan_id=9727ca8b-...（backend_python / 上海）
[10/18] PASS 用户正文导入岗位（标准化管道） (0.02s)
        用户正文导入岗位成功 canonical_job_id=15e5651e-...
[11/18] PASS 每日匹配任务生成推荐 (0.03s)
        匹配任务完成: {'status': 'ok', ..., 'evaluated': 1, 'hard_survivors': 1, 'created': 1, 'degraded': False}
[12/18] PASS 推荐列表 + 详情（硬条件/分项/证据） (0.02s)
        推荐 1 条；示例分数 81(high) 硬过滤=passed；分项 6 个（5 个带证据引用）；版本 score_v1
[13/18] PASS 触发深度分析（合成模型，如实 not_verified） (0.05s)
        深度分析完成 run_id=3e24b551-...（provider=synthetic, verified=false 如实标注；报告 schema=std_analysis_v1）
[14/18] PASS 岗位定制简历草稿（100% 事实绑定） (0.05s)
        定制草稿 version_id=cac77618-...；内容 6 行 100% 绑定已确认事实；调整记录 5 条均可追溯
[15/18] PASS 确认版本 → DOCX/PDF 导出下载 (0.17s)
        确认→导出→限时签名下载闭环通过；DOCX 37078B / PDF 2934B
[16/18] PASS 推荐反馈 (0.02s)
        推荐反馈提交并在列表可见（interested）
[17/18] PASS 个人数据导出（zip + 防篡改签名） (0.06s)
        数据导出 zip 6969B / 2 个文件；篡改签名被拒绝
[18/18] PASS 账号注销 → 宽限期 → 硬删清理闭环 (0.05s)
        注销闭环通过：宽限期阻写 → 到期硬删（manifest: resumes=1, plans=1, files_failed=0）→ 会话失效
------------------------------------------------------------------------------
结果：全部 18 步通过。本地端到端用户旅程验收 PASS。
注意：LLM/Embedding 为合成 Adapter，真实模型效果 not_verified，详见 docs/acceptance-report.md。
```

### 1.2 本地已验证的安全/隐私/来源边界（既有 217 测试 + 验收脚本双覆盖）

- 越权：推荐/事实/导出/数据导出跨用户访问 404（tests/integration/test_rbac.py、test_recommendations_api.py 等）
- 签名 URL：过期清理、篡改签名拒绝（验收脚本第 17 步实测 404）
- 上传安全：伪装 MIME / magic bytes / 超大 / 加密 / 损坏 / 页数超限全部拒绝（test_resume_upload.py 11 类样本）
- 受保护属性（性别/年龄/民族/籍贯/婚姻等）：解析即丢弃、不进候选、不进向量文本、不进定制简历（脚本第 7 步 + test_embedding_and_vector_text.py + test_resume_tailoring.py）
- 虚构拦截：定制简历内容 100% 绑定已确认事实 ID，未确认事实/编造数字 422（脚本第 14 步 + test_resume_tailoring.py）
- 日志隐私：请求/审计日志无简历正文、联系方式、token（test_log_and_audit_privacy.py 敏感串扫描）
- 授权：DeepSeek/Qwen 分别授权、撤回即拒、不批量、notice 版本校验（test_consents_guard.py）
- 删除完整性：注销→宽限期阻写→到期硬删（DB 行 + 存储文件 + 向量 + 会话），manifest 可验证，账本匿名化保留（脚本第 18 步 + test_account_purge.py）
- 诚实标注：`/health/capabilities` 中 deepseek/qwen/embedding 等均为 `not_verified`，恶意扫描如实报 `skipped_not_configured`（脚本第 4/7 步有 Gate 断言，虚标会导致验收失败）

### 1.3 阶段 9 验收过程中发现并修复的缺陷（小修，见 git diff）

| 缺陷 | 位置 | 修复 |
|---|---|---|
| 后端无 CORS 中间件，浏览器前端（:3000/:3901）调 API 会被拦截，浏览器端登录不可用 | `backend/app/main.py` | 增加 CORSMiddleware，只放行 `FRONTEND_BASE_URL` 配置的来源并允许携带 cookie |
| 登录页字段与后端契约不符：`/auth/invites/validate` 发 `invite_code`（应为 `code`）、magic-links 发 `age_confirmed/terms_accepted`（应为 `age_attested`），实际会 422 | `frontend/src/app/login/page.tsx` | 按 docs/04 契约改字段 |
| 建档页授权 POST /consents 缺少必填 `notice_version`，实际会 422 | `frontend/src/app/onboarding/page.tsx` | 先 GET /consents/notices 再带当前版本提交 |

这三个缺陷说明此前前端↔后端从未在真实浏览器里联调过——正是 E2E 补上的证据缺口。修复后浏览器 E2E 登录旅程实测通过（第 1 节 #5）。

---

## 2. 各阶段规格对照（做了什么 / 推迟了什么 + ADR 依据）

| 阶段 | 规格要求 | 状态 | 裁剪/推迟依据 |
|---|---|---|---|
| 1 脚手架 | monorepo、Compose(PG/pgvector+Redis+Mailpit)、Alembic、健康端点、结构化日志 | 已实现，本地验证 | — |
| 2 认证隐私 | 邀请码、magic link、会话、RBAC、18 岁确认、授权/撤回、审计、7 天注销 | 已实现，本地验证 | — |
| 3 简历事实 | 上传校验、解析、候选事实、用户确认、provenance | 已实现，本地验证 | 恶意文件扫描引擎未接（如实标 `skipped_not_configured`） |
| 4 方案岗位 | ≤3 方案、快照不可变、标准化、去重、导入、BOSS link-out | 已实现，本地验证 | 连接器为 fixture（`not_verified`），ADR-001 D1：导入为主、连接器非阻断 |
| 5 匹配推荐 | 硬过滤三态、六维评分、pgvector 召回、每日 20、反馈 | 已实现，本地验证 | Embedding 为确定性合成向量（`det-hash-768@1`），真实语义质量 not_verified |
| 6 AI 分析 | Model Gateway、标准分析、额度、降级 | 已实现（单模型版），本地验证 | ADR-001 D2：LangGraph 多 Agent 深度分析**推迟到本地 MVP 之后**；合成 provider，verified=false |
| 7 定制导出 | 定制版本、事实绑定、确认、DOCX/PDF | 已实现，本地验证 | ADR-001 D2：模板从 2-3 套减为 **1 套标准单栏**；渲染人工视觉检查未做（见第 3 节） |
| 8 管理隐私运维 | 只读管理后台 + CLI、数据导出、注销清理 | 已实现，本地验证 | ADR-001 D2：管理后台减配为只读监控 + CLI |
| 9 最终验收 | 验收脚本、E2E、60 组评测、7 天稳定性、演练 | 本报告 | E2E 按 ADR-001 D2 裁剪为核心旅程 2 条（其余旅程由验收脚本在 API 层全量覆盖）；60 组人工标注、7 天稳定性、备份/熔断演练**未做**，见第 3 节 |
| ADR-001 D4 | FR-REC-06 每日推荐摘要邮件 | **未实现** | 遗留待办（01/04/05 文档亦未补），列入第 4 节已知限制 |
| ADR-001 D5 | 60 份合成简历评测集 | 部分：10 份合成简历 + 20 合成岗位跑出基线（evaluation/baseline-report.md，200 对，硬条件误放 0，对偶 10/10 一致） | 60 份全量生成与人工标注未做 |

---

## 3. 未验证 / not_verified（如实声明，无一例外）

以下各项**没有任何本地测试数量可以替代**（docs/09 第 1、13 节）：

| 项目 | 状态 | 说明 |
|---|---|---|
| 真实 LLM 效果（DeepSeek/Qwen） | **not_verified** | 无真实 API key；全链路使用确定性合成 Adapter（provider=synthetic），API 响应中 `verified=false` 如实标注 |
| 真实 Embedding（阿里云） | **not_verified** | 合成向量 `det-hash-768@1`；语义召回质量未经真实模型验证 |
| 真实邮件送达 | **not_verified** | 仅 Mailpit 本地 SMTP 捕获验证了发信路径；真实邮箱（收件率/垃圾箱）未验证，capabilities 端点亦标 not_verified |
| 60 组人工标注评测（Top10≥70%、误放<1% 等门槛） | **未执行** | 需负责人人工标注；现有 baseline（阶段 5）只能做回归基线，不能替代离线门槛验收 |
| 使用负责人真实简历的真实模型流程（DoD 第 5 条） | **未执行** | 依赖真实 LLM key |
| 来源连接器真实运行 / 7 天稳定性 | **not_verified** | 连接器为 fixture；无 7 天真实运行数据 |
| 生产部署（阿里云 ECS/OSS/HTTPS/域名） | **未执行** | 无云端资源 |
| ICP 备案 | **未启动** | ADR-001 D3 要求第 8 周前启动个人备案，Beta 阻断项 |
| 备份/恢复、预算熔断、降级桌面演练 | **未执行** | 预算熔断逻辑有集成测试，但演练（含备份恢复）未做 |
| 隐私政策/用户协议专业复核 | **未执行** | 现为占位文本 + 版本号，Beta 前必须复核（docs/08） |
| 20～50 人 Beta 指标 | **未执行** | 未上线 |
| 依赖/密钥扫描工具链（pip-audit/gitleaks 类） | **未执行** | 阶段 1 CI 规格项，本机未安装扫描器；密钥管理靠 .env 约定 |
| DOCX/PDF 渲染人工视觉检查（docs/09 第 8 节） | **未执行** | 自动测试验证了文件可打开、文本可提取、事实一致；中文分页/长链接等视觉项需人工截图检查 |

**结论**：本地 Definition of Done（docs/09 第 12 节）中「使用负责人简历完成一次真实模型流程」「60 组评测达标」两条未满足，因此准确表述为：**「本地 MVP（合成模型）验收通过」；完整本地 DoD 与 Beta Go 均为 No-go**，最短路径见第 5 节。

---

## 4. 已知限制

1. FR-REC-06 每日推荐摘要邮件（ADR-001 D4）未实现。
2. 前端 `npm run lint` 存在 1 个**先于阶段 9 的**既有错误：`src/app/resume-versions/[id]/page.tsx:51` react-hooks/set-state-in-effect（HEAD `9649fc9` 上即存在，非本阶段引入；`npm run build` 不受影响）。
3. E2E 只覆盖登录/建档核心旅程 2 条（Chromium 单浏览器）；上传→事实→方案→推荐→定制→导出→注销在浏览器层未逐页走查，仅 API 层全量覆盖。手机视口未测。
4. 验收脚本用「把 purge_after 拨到过去」等价模拟 7 天宽限期到期（脚本内已注明），未做真实 7 天等待。
5. 验收脚本为 httpx ASGI 进程内调用 + Celery eager；独立 uvicorn+celery worker 的进程级拓扑由 E2E（uvicorn 真进程）与 `make dev` 手册覆盖，但 worker 独立进程下的异步行为（重试/死信时序）只有集成测试覆盖。
6. 岗位供给以用户导入为主（ADR-001 D1），fixture 连接器不产生真实岗位。

---

## 5. 阻断项与下一步最短路径

1. 配置真实 DeepSeek key → 用负责人本人简历跑一次真实模型流程（DoD 第 5 条）。
2. 生成 60 份合成简历全量评测集（ADR-001 D5）→ 负责人按 docs/09 第 4 节表单标注 → 出离线门槛结论。
3. 启动个人 ICP 备案（2-4 周周期，Beta 硬阻断）。
4. 补 FR-REC-06 摘要邮件；隐私政策/用户协议专业复核。
5. 云端：ECS/OSS/HTTPS 部署 + 备份恢复/熔断演练 + 7 天稳定性观察，才可讨论 Beta Go。

---

## 6. 阶段 9 文件清单（未提交，按要求不 commit）

- 新增 `backend/scripts/acceptance.py` — 端到端验收脚本（18 步，Gate 非零退出）
- 新增 `frontend/playwright.config.ts`、`frontend/e2e/global-setup.ts`、`frontend/e2e/login-journey.spec.ts` — 浏览器 E2E（独立库 careercopilot_e2e / Redis db3 / 端口 8901+3901）
- 修改 `backend/app/main.py` — 补 CORS（1.3 节缺陷修复）
- 修改 `frontend/src/app/login/page.tsx`、`frontend/src/app/onboarding/page.tsx` — 契约字段修复（1.3 节）
- 修改 `frontend/package.json`（+`@playwright/test`、`test:e2e` 脚本）、`package-lock.json`、`.gitignore`（playwright 产物）
- 新增 `docs/acceptance-report.md` — 本报告

---

## 7. 阶段 10 降级说明（2026-07-30，依据 ADR-002）

负责人任务书废弃了 ADR-001 D1（「用户导入为主、自动发现为辅、供给不足不阻塞 Beta」）。
据此，本报告的验收结论**降级**如下：

1. **不得再宣称「完整本地 MVP 验收通过」。** 自动发现是核心产品假设，真实岗位来源 Spike
   是 MVP 硬门槛；在至少一个真实合规来源被端到端验证（真实岗位进入推荐、形成书面 Spike
   结论）之前，本报告只能主张：**「合成模型 + 合成/导入岗位的本地演示」端到端通过**。
2. 第 1 节的实跑命令、退出码与数字是 `12be67e` 时点的历史结果，阶段 10 未重跑，仍然真实
   有效，但其证明范围仅限于「代码在本地可运行且旅程走通」，不构成 MVP/Beta 就绪证据。
3. 第 2 节表格中所有引用「ADR-001 D1」作为裁剪依据的行（阶段 4/阶段 9/已知限制第 6 条），
   其裁剪依据已失效；相应缺口（真实连接器、真实供给）回归为**阻断项**。
4. 阶段 10 任务 B 修复了本报告未覆盖的一个核心缺陷：用户导入岗位曾进入全局候选池并可被
   推荐给其他用户（跨用户泄露）。修复后：导入岗位默认 `private` 仅导入者可见，连接器公开
   岗位才进全局池；该修复的测试证据见阶段 10 交付记录，不追溯修改本报告第 1 节的历史数字。
5. Beta 就绪判定维持 **No-go**，且在 Spike 通过前不再复评。
