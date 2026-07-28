# 阶段 5：Embedding、基础匹配、证据与反馈

前置：阶段4已通过。阅读 `docs/07-ai-matching-agents.md` 中第1～6节，以及 `docs/09-testing-evaluation.md`。

## 目标

实现硬条件、阿里云 Embedding Gateway、pgvector 召回、版本化综合评分、证据、每日推荐和显式反馈。

## 必须交付

- 硬条件 `passed/failed/unknown`，覆盖城市、薪资、全职、外包、学历、经验、屏蔽公司。
- 经验差1年降分，差距过大过滤；学历 required/preferred 区分。
- Embedding Adapter/Gateway，授权/密钥/费用/超时/版本；无密钥时使用合成 Adapter 并标 `not_verified`。
- pgvector 索引和向量版本，禁止新旧模型混用。
- 初始评分权重、80/65等级、分项和证据引用。
- 每方案每日最多20个新推荐。
- 感兴趣/不感兴趣 + 结构化原因，派生偏好可重置。
- 受保护属性不进入向量与评分的确定性测试。

## 验收

- 建立六类岗位60组标注集的可执行格式和首轮基线报告。
- 硬条件误放、证据缺失、薪资未知、相似技能、反馈重置、重复推荐测试。
- 任何无证据结论必须是 `insufficient_evidence`，不得让 LLM 补写。
