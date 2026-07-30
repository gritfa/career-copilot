# 阶段 11：作品集收敛计划（Portfolio Convergence Plan）

- 日期：2026-07-30
- 依据：docs/14-adr-003-portfolio-pivot.md
- 目标：**陌生人 5 分钟跑起来、10 分钟看懂亮点**
- 状态：P0 等待负责人提供 DeepSeek API key，P1/P2 可先行

## 工作包

### P0：真实 DeepSeek 全链路验证（最高优先级，等 key）

前置：负责人提供 `DEEPSEEK_API_KEY`（写入本地 .env，绝不入库）。

1. Gateway 切真实 DeepSeek，实跑三条链路各≥1 次：
   - 标准分析（std_analysis_v1）：真实模型产出 + 证据校验通过（虚构引用拦截逻辑对真实模型同样生效）；
   - 定制简历（resume_content_v1）：真实模型产出 + 四道校验通过 + DOCX/PDF 导出；
   - 失败路径：故意给无效 key，确认明确 failed、不静默降级到合成。
2. 验证 usage_ledger 真实记账（token 数、费用估算非零）；日志无正文红线复查。
3. capabilities 端点 `standard_analysis` 从 not_verified 翻转为 verified 的条件与表述核对。
4. 留存证据：脱敏后的请求/响应摘要、报告截图、账本记录，写入 `docs/16-real-model-verification.md`。
5. Embedding 仍为确定性合成向量（DeepSeek 无 embedding 服务；阿里云 DASHSCOPE key 若负责人
   后续提供再切换，不阻塞本阶段），文档如实标注。

验收：三条链路证据齐全；测试仍全绿（真实 key 不进测试环境，测试继续用合成 Adapter）。

### P1：一键 Demo 种子（不依赖 key，可先做）

1. `backend/scripts/seed_demo.py`：幂等创建
   - 演示账号（demo@example.com，跳过邀请码或预生成邀请码）；
   - 1 份演示简历（合成人物，与 evaluation 合成简历同源风格）+ 已确认事实集；
   - 25-30 个种子示例岗位（覆盖 3 城市 × 4 方向，global 可见性，标注 `data_origin: seed`）；
   - 1 个搜索方案 + 实跑一轮匹配产出推荐。
2. `make demo`（或等价一条命令）：compose 起容器 → 迁移 → seed → 打印访问地址与演示账号登录方式。
3. 种子数据红线：全部合成内容，不含任何真实公司在招岗位正文、不含真实个人信息。

验收：干净环境 `git clone` 后 ≤3 条命令、≤5 分钟出可登录可点击的完整 demo（推荐列表带证据、
分析、定制简历导出全部可操作）。

### P2：文档作品集化（不依赖 key，可先做）

1. README 重写（作品集导向）：
   - 一段话定位 + 架构图（现有 docs/02 精简）+ 技术栈徽章；
   - 「快速开始」= P1 的 3 条命令；
   - 「工程亮点」小节（证据绑定防幻觉 / 受保护属性对偶测试 10/10 / 280+ 测试与 E2E /
     跨用户隐私修复 / 供给 Spike 合规核查 0/10 的如实报告）；
   - 「状态如实声明」：合成模型演示 vs 真实模型已验证范围（随 P0 更新）。
2. demo 截图/录屏（P0 完成后补真实模型截图）。
3. docs/11 交付计划标注 `dropped_portfolio_pivot` 条目（不删除，保留决策轨迹）。

验收：README 单页讲清楚项目；亮点均有仓库内证据链接（file/commit/报告）。

### P3（可选，负责人另行拍板）：公开演示部署

- 海外免费/低价平台（Fly.io / Render / Railway），避开 ICP 备案；
- 只部署 demo 种子数据，关闭注册或仅邀请码；
- 环境变量注入 key，费用告警上限。
- 不做 7 天稳定性承诺，页脚标注 portfolio demo。

## 明确不做（ADR-003）

爬虫/连接器/无头浏览器、条款判读、ICP、Beta、备份演练、隐私政策专业复核、
真实邮件送达、LangGraph 多 Agent、第二套简历模板、支付、自动投递。

## 执行顺序与依赖

P1 → P2 先行（无外部依赖），P0 等 key 到手立即插队执行，P3 等负责人单独拍板。
全部在 `feat/supply-spike-and-import-privacy` 后续分支或合并 main 后的新分支进行，
是否合并 main 由负责人决定。
