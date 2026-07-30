# 岗位供给 Spike 工具（ADR-002 硬门槛）

验证「每日自动从合规来源发现新岗位」这一核心产品假设是否成立。
**与生产连接器完全隔离**：实现在 `backend/app/supply_spike/`，不写业务数据库，
所有结果落本目录 `reports/` 为不可变 JSON。

## 命令

```bash
cd backend
uv run python scripts/run_supply_spike.py probe            # 只查政策与技术可达性（低频只读）
uv run python scripts/run_supply_spike.py collect          # 只跑 probe 判 verified 的来源
uv run python scripts/run_supply_spike.py report           # 从历史 runs 生成 Markdown+JSON 报告
# 通用选项：--dry-run（不发网络请求、不写文件，只打印将执行的动作）
#          --source KEY（只处理指定来源）
```

## 文件

- `sources.yaml` — 第一批 10 家中国公司公开招聘官网；`probe_result` 块由 probe 如实写入，
  `status ∈ verified / not_verified / blocked`。
- `thresholds.yaml` — 供给门槛（全部 `pending_owner_confirmation`，负责人可调；
  报告随附原始数据，绝不放宽数据口径）。
- `reports/runs/*.json` — 每次运行的不可变结果（重名拒写，绝不覆盖）。
- `reports/raw/{run_id}/` — collect 抓取的原始响应缓存（URL+时间+sha256+解析版本）。
- `reports/report-*.md|.json` — 汇总报告。

## 合规硬边界（绝对红线，代码内同样强制）

- 禁止抓取 BOSS 直聘正文；BOSS 只保留搜索跳转 + 用户导入。
- 禁止绕过登录/验证码/滑块/Cookie 墙/反爬；禁止模拟登录、代理池、指纹规避、打码服务。
- 官网自动请求条件（**全部满足**才允许）：无需登录、无验证码、robots.txt 与公开条款未禁止、
  可识别 UA（`CareerCopilotSpike/0.1 +联系方式占位`）、单域名 ≤1 请求/2 秒、
  超时/重试上限/熔断/缓存齐备、保存来源 URL+抓取时间+内容哈希+解析版本。
- 政策不清 = `not_verified` = 不自动访问（"政策不清默认允许"被明确禁止）。
- 条款页由工具抓存证据，但**是否构成许可由负责人人工判读**
  （`terms_manual_review: pending_owner_confirmation`），判读通过前到不了 verified。
- 网络不可用必须失败或标 `not_verified`，绝不回退 fixture 冒充成功；
  真实结果与测试 fixture 用 `data_origin: live|fixture` 显式区分。
- 第一天只能输出库存基线；观察不足 7 个自然日时 `supply_gate=not_verified`
  （`reason=insufficient_observation_days`），绝不伪造"每日新增"。
