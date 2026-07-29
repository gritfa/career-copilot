# 匹配引擎离线评测基线报告（阶段 5）

- 生成时间：2026-07-29 03:52 UTC
- 版本链：`score_v1` / `hard_v1` / embedding `det-hash-768@1` / 预处理 `p1`
- 输入：合成简历 10 份（evaluation/synthetic-resumes/，ADR-001 D5） × 合成岗位 20 个（app/evaluation/synthetic_jobs.py）
- **数据与效度声明（如实标注，不虚构）**：
  - 简历与岗位全部为合成数据，未采集任何真实简历或真实招聘平台岗位；
  - Embedding 为**确定性合成向量**（`det-hash-768@1`，特征哈希），**非真实模型结果**，语义召回质量 `not_verified`；
  - 词面评分为确定性规则，同输入同输出；本报告可作为回归基线，但**不能**替代真实模型 + 60 组人工标注集的离线门槛验收（docs/09 第 4、5 节）；
  - 报告不含简历正文、人名与联系方式，只含派生指标。

## 1. 总览

- 简历×岗位评估对：**200**；硬条件整体 passed **19** / uncertain **60** / failed **121**
- 产生推荐条目：**79**（每份简历上限 20/日，failed 岗位一律不进入）
- 硬条件误放（推荐中含 failed 条件的条目数）：**0**（门槛：0）
- 受保护属性对偶测试：**10/10 一致**（全部通过）

### 1.1 硬条件逐项分布（全部评估对）

| 条件 | passed | passed_with_penalty | failed | unknown |
|---|---:|---:|---:|---:|
| city | 151 | 0 | 49 | 0 |
| work_mode | 130 | 0 | 0 | 70 |
| salary | 70 | 0 | 70 | 60 |
| full_time | 190 | 0 | 10 | 0 |
| outsourcing | 140 | 0 | 60 | 0 |
| blocked_company | 200 | 0 | 0 | 0 |
| education | 188 | 0 | 12 | 0 |
| experience | 182 | 6 | 12 | 0 |

### 1.2 分项分数分布（全部推荐条目）

| 分项 | 权重 | min | mean | max | insufficient_evidence 次数 |
|---|---:|---:|---:|---:|---:|
| core_skills | 35 | 0 | 20.5 | 89 | 10 |
| experience | 20 | 60 | 71.6 | 100 | 0 |
| project_evidence | 20 | 0 | 24.7 | 98 | 41 |
| role_semantic | 10 | 0 | 21.2 | 100 | 0 |
| industry | 5 | 100 | 100 | 100 | 0 |
| preference | 10 | 70 | 74.8 | 90 | 0 |
| **score_total** | 100 | 24 | 41.0 | 95 | - |

## 2. 逐简历结果

### resume-01-ai-app（方向 ai_application，城市 北京）

- 画像（真实解析器派生）：学历 `master`，经验 `3.0` 年，技能事实 5 条；解析时受保护属性命中 2 处（只计数、全部丢弃）
- 硬条件：passed 2 / uncertain 6 / failed 12（共 20 岗）
- failed 明细：ai_application-C→salary+outsourcing; backend_java-A→city; backend_java-C→salary+outsourcing; backend_python-A→city; backend_python-C→salary+outsourcing; data-A→city; data-C→salary+outsourcing; frontend-A→city; frontend-C→salary+outsourcing; qa-A→city; qa-C→salary+outsourcing; unknown-X1→salary+full_time

| # | 岗位 | 硬状态 | 总分 | 等级 | core | exp | proj | role | ind | pref | 余弦 |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | ai_application-A | passed | 87 | high | 75 | 100 | 85 | 100 | 100 | 90 | 0.465 |
| 2 | ai_application-B | uncertain | 81 | high | 75 | 80 | 85 | 100 | 100 | 70 | 0.459 |
| 3 | backend_python-B | uncertain | 58 | low | 47 | 80 | 67 | 0 | 100 | 70 | 0.26 |
| 4 | backend_java-B | uncertain | 43 | low | 20 | 80 | 40 | 0 | 100 | 70 | 0.212 |
| 5 | qa-B | uncertain | 37 | low | 9 | 80 | 29 | 0 | 100 | 70 | 0.203 |
| 6 | data-B | uncertain | 36 | low | 8 | 80 | 28 | 0 | 100 | 70 | 0.17 |
| 7 | unknown-X2 | passed | 32 | low | 0† | 80 | 0† | 20† | 100 | 90 | 0.198 |
| 8 | frontend-B | uncertain | 28 | low | 0 | 80 | 0† | 0 | 100 | 70 | 0.215 |

### resume-02-ai-app（方向 ai_application，城市 杭州）

- 画像（真实解析器派生）：学历 `bachelor`，经验 `1.0` 年，技能事实 6 条；解析时受保护属性命中 2 处（只计数、全部丢弃）
- 硬条件：passed 1 / uncertain 6 / failed 13（共 20 岗）
- failed 明细：ai_application-A→city+experience; ai_application-C→salary+outsourcing; backend_java-A→city+experience; backend_java-C→salary+outsourcing; backend_python-A→city+experience; backend_python-C→salary+outsourcing; data-A→city+experience; data-C→salary+outsourcing; frontend-A→city+experience; frontend-C→salary+outsourcing; qa-A→city+experience; qa-C→salary+outsourcing; unknown-X1→salary+full_time

| # | 岗位 | 硬状态 | 总分 | 等级 | core | exp | proj | role | ind | pref | 余弦 |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | ai_application-B | uncertain | 64 | low | 50 | 60 | 60 | 100 | 100 | 70 | 0.191 |
| 2 | backend_python-B | uncertain | 44 | low | 29 | 60 | 49 | 0 | 100 | 70 | 0.108 |
| 3 | backend_java-B | uncertain | 39 | low | 20 | 60 | 40 | 0 | 100 | 70 | 0.125 |
| 4 | frontend-B | uncertain | 34 | low | 11 | 60 | 31 | 0 | 100 | 70 | 0.097 |
| 5 | qa-B | uncertain | 33 | low | 9 | 60 | 29 | 0 | 100 | 70 | 0.085 |
| 6 | data-B | uncertain | 32 | low | 8 | 60 | 28 | 0 | 100 | 70 | 0.102 |
| 7 | unknown-X2 | passed | 31 | low | 0† | 80 | 0† | 10† | 100 | 90 | 0.101 |

### resume-03-python-backend（方向 backend_python，城市 上海）

- 画像（真实解析器派生）：学历 `bachelor`，经验 `4.25` 年，技能事实 6 条；解析时受保护属性命中 2 处（只计数、全部丢弃）
- 硬条件：passed 3 / uncertain 6 / failed 11（共 20 岗）
- failed 明细：ai_application-A→city; ai_application-C→salary+outsourcing; backend_java-A→city; backend_java-C→salary+outsourcing; backend_python-C→salary+outsourcing; data-C→salary+outsourcing; frontend-A→city; frontend-C→salary+outsourcing; qa-A→city; qa-C→salary+outsourcing; unknown-X1→salary+full_time

| # | 岗位 | 硬状态 | 总分 | 等级 | core | exp | proj | role | ind | pref | 余弦 |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | backend_python-A | passed | 90 | high | 76 | 100 | 96 | 100 | 100 | 90 | 0.249 |
| 2 | backend_python-B | uncertain | 80 | high | 76 | 60 | 96 | 100 | 100 | 70 | 0.241 |
| 3 | backend_java-B | uncertain | 54 | low | 47 | 60 | 67 | 0 | 100 | 70 | 0.235 |
| 4 | data-A | passed | 47 | low | 17 | 100 | 37 | 0 | 100 | 90 | 0.182 |
| 5 | ai_application-B | uncertain | 44 | low | 30 | 60 | 50 | 0 | 100 | 70 | 0.236 |
| 6 | qa-B | uncertain | 38 | low | 18 | 60 | 38 | 0 | 100 | 70 | 0.265 |
| 7 | data-B | uncertain | 37 | low | 17 | 60 | 37 | 0 | 100 | 70 | 0.184 |
| 8 | unknown-X2 | passed | 32 | low | 0† | 80 | 0† | 15† | 100 | 90 | 0.15 |
| 9 | frontend-B | uncertain | 24 | low | 0 | 60 | 0† | 0 | 100 | 70 | 0.177 |

### resume-04-python-backend（方向 backend_python，城市 成都）

- 画像（真实解析器派生）：学历 `associate`，经验 `2.25` 年，技能事实 5 条；解析时受保护属性命中 2 处（只计数、全部丢弃）
- 硬条件：passed 1 / uncertain 6 / failed 13（共 20 岗）
- failed 明细：ai_application-A→city+education; ai_application-C→salary+outsourcing; backend_java-A→city+education; backend_java-C→salary+outsourcing; backend_python-A→city+education; backend_python-C→salary+outsourcing; data-A→city+education; data-C→salary+outsourcing; frontend-A→city+education; frontend-C→salary+outsourcing; qa-A→education; qa-C→salary+outsourcing; unknown-X1→salary+full_time

| # | 岗位 | 硬状态 | 总分 | 等级 | core | exp | proj | role | ind | pref | 余弦 |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | backend_python-B | uncertain | 44 | low | 29 | 60 | 0† | 100 | 100 | 70 | 0.254 |
| 2 | unknown-X2 | passed | 32 | low | 0† | 80 | 0† | 22† | 100 | 90 | 0.225 |
| 3 | backend_java-B | uncertain | 31 | low | 20 | 60 | 0† | 0 | 100 | 70 | 0.259 |
| 4 | ai_application-B | uncertain | 29 | low | 15 | 60 | 0† | 0 | 100 | 70 | 0.242 |
| 5 | data-B | uncertain | 27 | low | 8 | 60 | 0† | 0 | 100 | 70 | 0.238 |
| 6 | qa-B | uncertain | 27 | low | 9 | 60 | 0† | 0 | 100 | 70 | 0.203 |
| 7 | frontend-B | uncertain | 24 | low | 0 | 60 | 0† | 0 | 100 | 70 | 0.164 |

### resume-05-java-backend（方向 backend_java，城市 广州）

- 画像（真实解析器派生）：学历 `bachelor`，经验 `4.92` 年，技能事实 6 条；解析时受保护属性命中 2 处（只计数、全部丢弃）
- 硬条件：passed 2 / uncertain 6 / failed 12（共 20 岗）
- failed 明细：ai_application-A→city; ai_application-C→salary+outsourcing; backend_java-C→salary+outsourcing; backend_python-A→city; backend_python-C→salary+outsourcing; data-A→city; data-C→salary+outsourcing; frontend-A→city; frontend-C→salary+outsourcing; qa-A→city; qa-C→salary+outsourcing; unknown-X1→salary+full_time

| # | 岗位 | 硬状态 | 总分 | 等级 | core | exp | proj | role | ind | pref | 余弦 |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | backend_java-A | passed | 65 | potential | 60 | 100 | 0† | 100 | 100 | 90 | 0.397 |
| 2 | backend_java-B | uncertain | 55 | low | 60 | 60 | 0† | 100 | 100 | 70 | 0.376 |
| 3 | backend_python-B | uncertain | 36 | low | 35 | 60 | 0† | 0 | 100 | 70 | 0.327 |
| 4 | unknown-X2 | passed | 32 | low | 0† | 80 | 0† | 17† | 100 | 90 | 0.173 |
| 5 | ai_application-B | uncertain | 28 | low | 10 | 60 | 0† | 0 | 100 | 70 | 0.25 |
| 6 | data-B | uncertain | 24 | low | 0 | 60 | 0† | 0 | 100 | 70 | 0.193 |
| 7 | frontend-B | uncertain | 24 | low | 0 | 60 | 0† | 0 | 100 | 70 | 0.193 |
| 8 | qa-B | uncertain | 24 | low | 0 | 60 | 0† | 0 | 100 | 70 | 0.217 |

### resume-06-java-backend（方向 backend_java，城市 北京）

- 画像（真实解析器派生）：学历 `master`，经验 `3.42` 年，技能事实 5 条；解析时受保护属性命中 2 处（只计数、全部丢弃）
- 硬条件：passed 2 / uncertain 6 / failed 12（共 20 岗）
- failed 明细：ai_application-C→salary+outsourcing; backend_java-A→city; backend_java-C→salary+outsourcing; backend_python-A→city; backend_python-C→salary+outsourcing; data-A→city; data-C→salary+outsourcing; frontend-A→city; frontend-C→salary+outsourcing; qa-A→city; qa-C→salary+outsourcing; unknown-X1→salary+full_time

| # | 岗位 | 硬状态 | 总分 | 等级 | core | exp | proj | role | ind | pref | 余弦 |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | backend_java-B | uncertain | 71 | potential | 53 | 80 | 73 | 100 | 100 | 70 | 0.153 |
| 2 | data-B | uncertain | 55 | low | 42 | 80 | 62 | 0 | 100 | 70 | 0.073 |
| 3 | backend_python-B | uncertain | 45 | low | 24 | 80 | 44 | 0 | 100 | 70 | 0.107 |
| 4 | ai_application-A | passed | 44 | low | 10 | 100 | 30 | 0 | 100 | 90 | 0.144 |
| 5 | ai_application-B | uncertain | 38 | low | 10 | 80 | 30 | 0 | 100 | 70 | 0.13 |
| 6 | unknown-X2 | passed | 31 | low | 0† | 80 | 0† | 12† | 100 | 90 | 0.119 |
| 7 | frontend-B | uncertain | 28 | low | 0 | 80 | 0† | 0 | 100 | 70 | 0.133 |
| 8 | qa-B | uncertain | 28 | low | 0 | 80 | 0† | 0 | 100 | 70 | 0.108 |

### resume-07-frontend（方向 frontend，城市 深圳）

- 画像（真实解析器派生）：学历 `bachelor`，经验 `3.0` 年，技能事实 6 条；解析时受保护属性命中 2 处（只计数、全部丢弃）
- 硬条件：passed 2 / uncertain 6 / failed 12（共 20 岗）
- failed 明细：ai_application-A→city; ai_application-C→salary+outsourcing; backend_java-A→city; backend_java-C→salary+outsourcing; backend_python-A→city; backend_python-C→salary+outsourcing; data-A→city; data-C→salary+outsourcing; frontend-C→salary+outsourcing; qa-A→city; qa-C→salary+outsourcing; unknown-X1→salary+full_time

| # | 岗位 | 硬状态 | 总分 | 等级 | core | exp | proj | role | ind | pref | 余弦 |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | frontend-A | passed | 95 | high | 89 | 100 | 98 | 100 | 100 | 90 | 0.26 |
| 2 | frontend-B | uncertain | 85 | high | 89 | 60 | 98 | 100 | 100 | 70 | 0.227 |
| 3 | backend_python-B | uncertain | 35 | low | 12 | 60 | 32 | 0 | 100 | 70 | 0.11 |
| 4 | unknown-X2 | passed | 31 | low | 0† | 80 | 0† | 14† | 100 | 90 | 0.14 |
| 5 | ai_application-B | uncertain | 24 | low | 0 | 60 | 0† | 0 | 100 | 70 | 0.118 |
| 6 | backend_java-B | uncertain | 24 | low | 0 | 60 | 0† | 0 | 100 | 70 | 0.149 |
| 7 | data-B | uncertain | 24 | low | 0 | 60 | 0† | 0 | 100 | 70 | 0.07 |
| 8 | qa-B | uncertain | 24 | low | 0 | 60 | 0† | 0 | 100 | 70 | 0.12 |

### resume-08-data-engineering（方向 data，城市 上海）

- 画像（真实解析器派生）：学历 `master`，经验 `3.83` 年，技能事实 5 条；解析时受保护属性命中 2 处（只计数、全部丢弃）
- 硬条件：passed 3 / uncertain 6 / failed 11（共 20 岗）
- failed 明细：ai_application-A→city; ai_application-C→salary+outsourcing; backend_java-A→city; backend_java-C→salary+outsourcing; backend_python-C→salary+outsourcing; data-C→salary+outsourcing; frontend-A→city; frontend-C→salary+outsourcing; qa-A→city; qa-C→salary+outsourcing; unknown-X1→salary+full_time

| # | 岗位 | 硬状态 | 总分 | 等级 | core | exp | proj | role | ind | pref | 余弦 |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | data-A | passed | 72 | potential | 50 | 100 | 53 | 100 | 100 | 90 | 0.411 |
| 2 | data-B | uncertain | 66 | potential | 50 | 80 | 53 | 100 | 100 | 70 | 0.376 |
| 3 | backend_python-A | passed | 47 | low | 18 | 100 | 32 | 0 | 100 | 90 | 0.196 |
| 4 | backend_python-B | uncertain | 41 | low | 18 | 80 | 32 | 0 | 100 | 70 | 0.152 |
| 5 | backend_java-B | uncertain | 39 | low | 13 | 80 | 33 | 0 | 100 | 70 | 0.087 |
| 6 | ai_application-B | uncertain | 36 | low | 10 | 80 | 25 | 0 | 100 | 70 | 0.196 |
| 7 | unknown-X2 | passed | 32 | low | 0† | 80 | 0† | 19† | 100 | 90 | 0.186 |
| 8 | qa-B | uncertain | 31 | low | 9 | 80 | 0† | 0 | 100 | 70 | 0.161 |
| 9 | frontend-B | uncertain | 28 | low | 0 | 80 | 0† | 0 | 100 | 70 | 0.149 |

### resume-09-qa-test（方向 qa，城市 成都）

- 画像（真实解析器派生）：学历 `associate`，经验 `1.0` 年，技能事实 7 条；解析时受保护属性命中 2 处（只计数、全部丢弃）
- 硬条件：passed 1 / uncertain 6 / failed 13（共 20 岗）
- failed 明细：ai_application-A→city+education+experience; ai_application-C→salary+outsourcing; backend_java-A→city+education+experience; backend_java-C→salary+outsourcing; backend_python-A→city+education+experience; backend_python-C→salary+outsourcing; data-A→city+education+experience; data-C→salary+outsourcing; frontend-A→city+education+experience; frontend-C→salary+outsourcing; qa-A→education+experience; qa-C→salary+outsourcing; unknown-X1→salary+full_time

| # | 岗位 | 硬状态 | 总分 | 等级 | core | exp | proj | role | ind | pref | 余弦 |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | qa-B | uncertain | 47 | low | 36 | 60 | 0† | 100 | 100 | 70 | 0.395 |
| 2 | unknown-X2 | passed | 32 | low | 0† | 80 | 0† | 24† | 100 | 90 | 0.237 |
| 3 | backend_python-B | uncertain | 28 | low | 12 | 60 | 0† | 0 | 100 | 70 | 0.116 |
| 4 | data-B | uncertain | 27 | low | 8 | 60 | 0† | 0 | 100 | 70 | 0.102 |
| 5 | ai_application-B | uncertain | 26 | low | 5 | 60 | 0† | 0 | 100 | 70 | 0.159 |
| 6 | backend_java-B | uncertain | 26 | low | 7 | 60 | 0† | 0 | 100 | 70 | 0.143 |
| 7 | frontend-B | uncertain | 24 | low | 0 | 60 | 0† | 0 | 100 | 70 | 0.116 |

### resume-10-test-dev（方向 qa，城市 深圳）

- 画像（真实解析器派生）：学历 `bachelor`，经验 `5.08` 年，技能事实 6 条；解析时受保护属性命中 2 处（只计数、全部丢弃）
- 硬条件：passed 2 / uncertain 6 / failed 12（共 20 岗）
- failed 明细：ai_application-A→city; ai_application-C→salary+outsourcing; backend_java-A→city; backend_java-C→salary+outsourcing; backend_python-A→city; backend_python-C→salary+outsourcing; data-A→city; data-C→salary+outsourcing; frontend-C→salary+outsourcing; qa-A→city; qa-C→salary+outsourcing; unknown-X1→salary+full_time

| # | 岗位 | 硬状态 | 总分 | 等级 | core | exp | proj | role | ind | pref | 余弦 |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | qa-B | uncertain | 71 | potential | 64 | 60 | 75 | 100 | 100 | 70 | 0.514 |
| 2 | backend_python-B | uncertain | 51 | low | 41 | 60 | 61 | 0 | 100 | 70 | 0.209 |
| 3 | backend_java-B | uncertain | 46 | low | 33 | 60 | 53 | 0 | 100 | 70 | 0.224 |
| 4 | data-B | uncertain | 37 | low | 17 | 60 | 37 | 0 | 100 | 70 | 0.181 |
| 5 | ai_application-B | uncertain | 36 | low | 15 | 60 | 35 | 0 | 100 | 70 | 0.209 |
| 6 | frontend-A | passed | 34 | low | 0 | 100 | 0† | 0 | 100 | 90 | 0.169 |
| 7 | unknown-X2 | passed | 32 | low | 0† | 80 | 0† | 20† | 100 | 90 | 0.197 |
| 8 | frontend-B | uncertain | 24 | low | 0 | 60 | 0† | 0 | 100 | 70 | 0.174 |

†：该分项带 uncertainty 标注（insufficient_evidence / vector_only 等），分值按规则保守给出。

## 3. 预埋缺陷核对（meta.seeded_flaws × 匹配层信号）

判定口径：只核对匹配基线（硬条件 + 词面 + 向量）**确定性可观察**的信号；
空窗期/跳槽/量化缺失等叙事类缺陷属深度分析阶段（阶段 6）职责，如实标注 `not_covered_by_matching`，不虚构命中。

| 简历 | 预埋缺陷 | 判定 | 证据/观察 |
|---|---|---|---|
| resume-01-ai-app | 第二、三个项目量化数据缺失，只有'显著降低''更加规范'等模糊表述 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-01-ai-app | 仅一段工作经历、单一公司环境，缺少跨团队/大规模系统经验佐证 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-02-ai-app | 技能堆砌无项目支撑：罗列 8 个 LLM 框架、5 个向量库、微调/推理全家桶，实际项目只用到 Dify、LangChain、Chroma 和简单 API 调用 | partially_observable | 词面层仅能对照核心技能与项目证据分项（ai_application-B core=50/project=60）；『无项目支撑』的强度判定属深度分析阶段（阶段 6） |
| resume-02-ai-app | 项目深度不足：工作项目为平台配置类工作，其余为毕设/课程级别，与'大模型应用工程师'岗位要求存在深度错配 | reflected | 本方向岗位核心技能覆盖存在缺口：ai_application-B core_skills=50 |
| resume-02-ai-app | 全部项目缺少量化结果数据 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-03-python-backend | 2024.04 – 2024.11 存在约 8 个月空窗期，简历中完全未提及、未解释 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-03-python-backend | 技能清单含错别字/混排（'факtory_boy'），拟真的粗心型笔误 | partially_observable | 本方向岗位存在词面未命中技术词：微服务, 性能优化, 消息队列, 高并发 |
| resume-03-python-backend | Kafka 仅'使用级'但列在中间件栏，易被追问深度 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-04-python-backend | 频繁跳槽：2 年内 3 家公司，单段最长仅 8 个月，且自评'长期稳定发展'与履历矛盾 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-04-python-backend | 量化数据缺失：全篇无一处具体数字指标，只有'明显提升''多个站点'等模糊描述 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-04-python-backend | 经历偏爬虫/CRUD，与'后端开发工程师'岗位的中间件、高并发要求存在深度差距 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-05-java-backend | 量化数据几乎完全缺失：5 年经验却无 QPS/延迟/订单量/故障率等任何数字，'明显减少''稳定运行'均不可验证 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-05-java-backend | 项目描述职责化：大量'负责''参与''维护'的职责罗列，缺少个人产出与难点决策 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-05-java-backend | 技能栏写'精通'Spring Cloud 全家桶，但项目描述看不出 Sentinel/Seata 的实际落地场景，存在堆砌嫌疑 | partially_observable | 词面层仅能对照核心技能与项目证据分项（backend_java-A core=60/project=0; backend_java-B core=60/project=0）；『无项目支撑』的强度判定属深度分析阶段（阶段 6） |
| resume-06-java-backend | 2024.02 – 2024.07 存在 6 个月空窗期，简历未解释（仅在概况里用'扣除空窗'一笔带过） | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-06-java-backend | 技能与目标岗位部分错配：近 4 年经验中一半是大数据平台方向（Spark/Flink/Hive），纯 Java 业务后端经验实际只有约 2 年 | reflected | 本方向岗位核心技能覆盖存在缺口：backend_java-B core_skills=53 |
| resume-06-java-backend | 第一段经历（衡数科技）1.5 年即离职，与第二段之间的转向原因未说明 | not_covered_by_matching | 无对应的 matching 层确定性信号；不虚构判定 |
| resume-07-frontend | 技能堆砌无项目支撑：技能栏列出 React、Angular、Flutter、Electron、React Native、Three.js，但全部项目经历只用到 Vue3 + uni-app 技术栈 | partially_observable | 词面层仅能对照核心技能与项目证据分项（frontend-A core=89/project=98; frontend-B core=89/project=98）；『无项目支撑』的强度判定属深度分析阶段（阶段 6） |
| resume-07-frontend | 单一公司单一业务线经验，缺少多环境适应性证据 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-08-data-engineering | 频繁跳槽：4 年 3 家公司，单段最长 17 个月，且分析→数据开发→数据工程的转向未解释 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-08-data-engineering | Flink 标注'入门级'却出现在核心技能栏，实时计算能力与数据工程岗普遍要求存在缺口 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-09-qa-test | 技能与目标岗位错配：求职意向为'测试开发'，但全部实战经历为手工功能测试，Python/Selenium 仅培训课程水平，无生产自动化项目支撑 | reflected | 本方向岗位核心技能覆盖存在缺口：qa-B core_skills=36 |
| resume-09-qa-test | 量化数据缺失：'多个边界问题''部分接口'等均无数字，无用例数量、缺陷数量、覆盖率等指标 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-09-qa-test | 大专学历 + 1 年经验，与测试开发岗普遍本科+自动化经验的硬性要求存在门槛差距 | reflected | 本方向岗位学历硬条件 failed：qa-A |
| resume-10-test-dev | 2024.09 – 2025.06 存在 10 个月空窗期，简历完全未提及、未解释 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |
| resume-10-test-dev | Java 仅'可读写业务代码'，与部分测试开发岗要求的 Java 技术栈深度存在差距 | not_covered_by_matching | 匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责 |

小结：not_covered_by_matching×18，partially_observable×4，reflected×4。

## 4. 受保护属性对偶测试

方法：同一简历只改受保护属性（性别互换、年龄 +8、追加婚育/民族/籍贯行），
重跑完整管道，要求向量文本哈希、硬条件状态、总分与全部分项**逐项一致**；
任一简历不一致则本命令以非零码退出。

| 简历 | 结果 | 差异 |
|---|---|---|
| resume-01-ai-app | 一致 ✅ | - |
| resume-02-ai-app | 一致 ✅ | - |
| resume-03-python-backend | 一致 ✅ | - |
| resume-04-python-backend | 一致 ✅ | - |
| resume-05-java-backend | 一致 ✅ | - |
| resume-06-java-backend | 一致 ✅ | - |
| resume-07-frontend | 一致 ✅ | - |
| resume-08-data-engineering | 一致 ✅ | - |
| resume-09-qa-test | 一致 ✅ | - |
| resume-10-test-dev | 一致 ✅ | - |

## 5. 与离线门槛（docs/09 第 5 节）的对照

- 硬条件误放率：0/79 → 满足 <1% 门槛（合成岗位集内可完全核验）
- 受保护属性对偶分差：0（全部一致）
- 证据可追溯率：全部分项要么带 evidence_refs 要么显式 `insufficient_evidence`（由匹配引擎单元/集成测试与本基线共同约束）
- Top 10 Precision ≥70%：**not_verified** —— 需真实模型 embedding + 60 组人工标注集，本报告的确定性合成向量不能替代该验收。
