# 真实模型验证报告（阶段 11 P0：DeepSeek 全链路实跑）

- 日期：2026-07-30（UTC 时间戳以数据库/日志为准）
- 环境：本机 macOS demo 环境（`./demo.sh` 编排：uvicorn + Celery worker/beat + Next.js + PG/Redis/Mailpit 容器）
- 供应商 / 模型：DeepSeek `deepseek-chat`（OpenAI 兼容 `/chat/completions`，`response_format=json_object`，temperature=0）
- key 来源：`backend/.env`（git-ignored）。**本报告与仓库任何跟踪文件、日志均不含 key**（见第 6 节复查）
- 验证账号：demo 种子用户（合成人物简历 + 合成种子岗位），登录走正常邀请码 + Mailpit magic-link 流程，无任何认证后门
- 授权：通过 `POST /api/v1/consents` 正常授予 `deepseek + profile_fields`（consent id `86abaedb`，notice `deepseek-2026-07-28.1`）；所有链路走既有授权门控，未绕过

## 结论速览

| 链路 | 结果 | 真实 token 用量（in/out） |
|---|---|---|
| ① 标准分析（agents 编排 + 证据校验） | ✅ completed（首跑暴露 prompt 缺陷 → 修复后通过） | 4494 / 1289 |
| ② 定制简历生成 + 四道校验 + DOCX/PDF 导出 | ✅ draft → confirmed → 双格式导出下载成功 | 3967 / 1719 |
| ③ 无效 key 失败路径 | ✅ 2.1s 干净失败，无重试/无泄露/无假成功/不降级合成 | 0 / 0（401 拒绝） |

Embedding 仍为**确定性合成向量**（`DeterministicHashEmbeddingAdapter`），本次未验证、也未改口径。

## 1. 链路①：标准分析（真实模型 + 证据校验）

### 1.1 首跑失败（如实记录）

- run `5c60bb59-e03f-4946-9b92-a89ee0cf0ebe`：`failed / SCHEMA_INVALID`
- worker 日志（只含错误类别，无正文）：
  ```
  16:42:14 llm_schema_failed error_note=resume_suggestions.0:model_type;resume_suggestions.1:model_type;
           resume_suggestions.2:model_type model_id=deepseek-chat provider=deepseek schema_name=std_analysis_v1
  ```
- 根因：`std_prompt_v1` 只列顶层字段名、未给嵌套对象结构，真实模型把
  `resume_suggestions` 输出成字符串数组。合成 Adapter 从不读 prompt，
  该缺陷在真实模型接入前不可能暴露——这正是 P0 的目的。
- 修复：prompt 内嵌 pydantic 生成的输出 JSON Schema（`std_prompt_v2` /
  `tailor_prompt_v2`），commit `88907df`。
- 附带暴露的第二个缺陷：该失败 run 实际消耗了供应商 token 但未写
  usage_ledger（丢账）。修复：`LLMSchemaError` 携带真实用量、失败也记账，
  commit `118d87d`。

### 1.2 修复后通过

- run `9d2d0f31-d625-4919-b994-930810791d46`，输入指纹 `291d53827971349b…`（与失败 run 同输入，可对比复现）
- 状态流转：queued → analyzing → validating → **completed**（08:46:59.68Z → 08:47:08.66Z，约 9 秒）
- worker 日志：
  ```
  16:47:08 llm_completed attempts=1 model_id=deepseek-chat provider=deepseek repaired=False
           schema_name=std_analysis_v1 tokens_in=4494 tokens_out=1289
  16:47:08 analysis_run_completed gaps=0 risks=0 strengths=9 …
  ```
- **确定性证据校验（validating 阶段）对真实模型输出真实生效并通过**：
  9 条 strengths 全部引用输入内 fact_id、job_span 逐字校验通过；3 条
  resume_suggestions 均绑定事实或显式 uncertainty；报告结构 `std_analysis_v1`
- usage_ledger 真实记账行：
  ```
  provider=deepseek model=deepseek-chat operation_type=llm_analysis
  tokens_in=4494 tokens_out=1289 amount_estimated=0.019300 occurred_at=2026-07-30 08:47:08Z
  ```
- API 返回 `verified=true` 语义：仅表示"该 run 的产出来自真实供应商"（按
  run.provider 判断），合成 run 恒为 false

## 2. 链路②：定制简历生成 + 校验 + 导出

- resume_version `fa1066ed-0dc9-424c-b50e-ce607f6675a1`，
  `generator_json = {"provider": "deepseek", "model_id": "deepseek-chat", "prompt_version": "tailor_prompt_v2"}`
- 状态流转：generating → **draft**（约 10 秒）→ 用户确认 → **confirmed**
- worker 日志：
  ```
  16:52:01 llm_completed attempts=1 model_id=deepseek-chat provider=deepseek repaired=False
           schema_name=resume_tailor_v1 tokens_in=3967 tokens_out=1719
  16:52:01 resume_tailor_completed changes=3 sections=4 …
  ```
- 内容结构：4 个 section（summary 1 / skills 6 / work_experience 2 / education 1 条目）+ 3 条 changes；
  确定性校验（事实引用 ⊆ 已确认事实、数字一致性、job_span 逐字、受保护属性拦截）对真实输出全部通过
- 导出（确认后触发，限时签名下载）：
  - DOCX export `8f1b5e16`：succeeded，下载 37420 字节，`file` 识别为 Microsoft OOXML
  - PDF export `4b869d0c`：succeeded，下载 3475 字节，`file` 识别为 PDF 1.3（1 页）
- usage_ledger 真实记账行：
  ```
  provider=deepseek model=deepseek-chat operation_type=llm_tailor
  tokens_in=3967 tokens_out=1719 amount_estimated=0.021686 occurred_at=2026-07-30 08:52:01Z
  ```

## 3. 链路③：无效 key 失败路径

- 方式：与 Celery 任务同一代码路径（`execute_standard_analysis` + 同步 Session +
  同一数据库），环境变量注入假 key `sk-invalid-p0-…`（不改任何文件），
  consent 门控照常走
- run `daa9bca3-f277-41a4-aad5-11def3710af6`，耗时 **2.10s**，结果：
  ```
  run.status=failed error_code=MODEL_UNAVAILABLE provider=deepseek
  report_is_none=True tokens_in=0 tokens_out=0
  ```
- 断言全部通过：
  - 明确 failed（`MODEL_UNAVAILABLE`），**不静默降级合成**（有 key 即选真实
    Adapter，provider 保持 deepseek，无报告落库）；
  - 401 → `LLMProviderRejectedError` **不重试**（日志无 `llm_call_retry`；
    v1 行为会盲目重试 2 次，commit `e06e634` 修复）；
  - 捕获的全部日志中**不含假 key 字符串**（异常信息只有 `HTTP 401`）；
  - 失败不影响既有规则+向量基础匹配结果（降级语义，docs/07 第 12 节）。

## 4. capabilities 三态（docs/15 P0 第 3 条的实现）

`GET /health/capabilities` 中模型能力为三个相互独立的维度（`app/api/health.py`）：

1. `configured` —— 当前进程环境有 key（不代表 key 有效）；
2. `runtime` —— 最近一次运行探测（`GET /models`，零 token，TTL 600s 缓存）：
   `available / unavailable / not_probed`；
3. `last_verified` —— 本报告对应的历史实跑证据
   `{verified_at: 2026-07-30, model_id: deepseek-chat, evidence: docs/16-…}`。

汇总 `status` 只由前两态推导（`not_configured / configured / available / unavailable`）——
**换一台没有 key 的机器，即使 `last_verified` 仍在，状态就是 `not_configured`**，
不存在"跑过一次永久 verified"（有测试钉死：`tests/test_health.py::test_verified_evidence_never_upgrades_current_status`）。
本机实测（有 key + 探测通过）：`status=available, runtime_checked_at=2026-07-30T08:41:18Z`。

`standard_analysis / docx_export / pdf_export` 标 `ready` 指**管线**（编排、授权、
校验、渲染）已经真实模型端到端验证；当前产出是否来自真实模型由
`deepseek_generation` 三态如实反映（无 key 时 `active_adapter=synthetic`）。

## 5. P0 实跑暴露并修复的问题清单

| 问题 | 暴露方式 | 修复 commit |
|---|---|---|
| 测试环境泄用真实 key：conftest 只 pop 环境变量，`backend/.env` 经 pydantic env_file 仍被加载 | 配好 key 后跑存量测试即失败 | `80c8a05` |
| 无效 key 401 被当可重试错误盲目重试 2 次 | 失败路径设计审查 + 实跑 | `e06e634` |
| prompt 只列顶层字段名，真实模型输出嵌套结构错误 → SCHEMA_INVALID | 链路①首跑 | `88907df` |
| 定制简历默认 2048 输出 token 有截断风险 | 同上（预防性，请求级提至 4096） | `88907df` |
| Schema 失败的 run 真实消耗 token 但不写 usage_ledger（丢账） | 链路①首跑账本核对 | `118d87d` |

## 6. 红线复查（脱敏声明）

- 真实 key 检索：`.demo/logs/` 全部日志 **0 命中**；git 跟踪文件（HEAD）**0 命中**（gitleaks 亦在 CI 常态运行）；
- worker 日志抽查：LLM 相关日志仅含 ID/计数/错误类别/模型名，无简历内容、无岗位正文、无 prompt、无模型响应正文；
- 本报告只含请求元数据、响应结构统计、账本行与结论；**不含**完整简历内容、完整 prompt、API key；
- 失败结果如实保留（链路①首跑 SCHEMA_INVALID 的 run 记录仍在库中，未删改）。
