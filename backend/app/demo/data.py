"""合成演示数据定义（阶段 11 P1）。

- 演示账号 / 邀请码常量：demo 登录走完全正常的邀请码 + magic link 流程，
  这里只是预置数据，不存在任何认证后门。
- 合成简历：虚构人物「林岸舟」，内容按规则解析器（app/resumes/parser.py）的
  抽取模式书写，保证能产出教育/工作经历/技能候选事实。
- 种子岗位：3 城市 × 4 方向共 28 个，全部为虚构公司与合成正文；
  data_origin='synthetic_seed'，前端展示「合成示例」徽标。
"""

import io
from typing import TypedDict

from docx import Document

# ---------------- 演示账号与邀请码 ----------------

# email-validator 拒绝 .test/.example 等特殊用途域名，这里用普通形态的合成域名
DEMO_EMAIL = "demo@careercopilot-demo.dev"
# 注册用一次性邀请码（max_uses=1，seed 注册时按正常流程消耗）
DEMO_INVITE_REGISTER = "DEMO-SEED-REG-2026"
# 登录用邀请码（已注册用户请求 magic link 仍需有效邀请码；
# 既有用户登录不消耗使用次数，max_uses=1 只允许它再注册 1 个新账号）
DEMO_INVITE_LOGIN = "DEMO-LOGIN-2026"

DEMO_PLAN_NAME = "演示方案：Python 后端（北/上/深）"

# ---------------- 合成简历（虚构人物） ----------------

DEMO_RESUME_FILENAME = "demo_resume_synthetic.docx"

# 行文严格贴合规则解析器：
# - 教育行 = 同一行出现「××大学」+「本科」
# - 工作行 = 时间段 + 「××公司」，不含学校词
# - 技能段 = 「专业技能」标题后的 “- 名称：详情” 条目
DEMO_RESUME_LINES: tuple[str, ...] = (
    "林岸舟（合成示例人物，仅用于产品演示）",
    "求职意向：Python 后端开发工程师",
    "教育背景",
    "2015.9 - 2019.6 沧澜大学 计算机科学与技术 本科",
    "工作经历",
    "2019.7 - 2022.6 雾屿信息技术有限公司 Python 后端开发工程师",
    "负责订单与结算服务的 FastAPI 接口开发，使用 PostgreSQL 与 Redis，"
    "基于 Celery 构建异步任务，压测与性能优化经验丰富。",
    "2022.7 - 至今 澄川数据科技有限公司 高级后端开发工程师",
    "主导内容平台服务拆分与高并发改造，落地消息队列削峰，"
    "维护 Docker/Kubernetes 部署与 CI 质量门禁，参与大模型 RAG 检索服务开发。",
    "专业技能",
    "- Python：FastAPI、Django、asyncio、pydantic、pytest 单元与接口自动化",
    "- 数据存储：PostgreSQL、MySQL、Redis、Elasticsearch、SQL 调优与分库分表",
    "- 异步与消息：Celery、Kafka、消息队列、高并发场景性能优化",
    "- 工程化：Docker、Kubernetes、CI、GitLab 流水线与质量门禁",
    "- AI 应用：LLM、RAG、LangChain、向量数据库、提示工程、Agent 工具调用",
    "- 数据方向：Spark、数仓建模、ETL、数据分析基础",
)


def build_demo_resume_docx() -> bytes:
    """生成合成简历 DOCX 字节流（内容全虚构，无受保护属性、无真实个人信息）。"""
    document = Document()
    for line in DEMO_RESUME_LINES:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------- 种子岗位（3 城市 × 4 方向 = 28 个，全部合成） ----------------

SEED_CITY_NAMES: tuple[str, ...] = ("北京", "上海", "深圳")

_SYNTHETIC_NOTE = "（本岗位为合成示例数据，仅用于 CareerCopilot 产品演示，非真实在招岗位。）"


class SeedJobPayload(TypedDict):
    """与连接器/导入同一管道的原始载荷字段（app/jobs/pipeline._parse_payload）。"""

    title: str
    company: str
    city: str
    salary: str
    experience: str
    education: str
    employment: str
    description: str
    published_at: str


class _JobSpec(TypedDict):
    title: str
    role_family: str
    salary: str
    experience: str
    education: str
    description: str


# 每方向 2-3 个岗位模板；title 需能被 normalize_role_family 可靠分类
_JOB_SPECS: tuple[_JobSpec, ...] = (
    # ---- backend_python（演示方案主方向，多给 1 个模板） ----
    {
        "title": "Python 后端开发工程师",
        "role_family": "backend_python",
        "salary": "25-40K·14薪",
        "experience": "3-5年",
        "education": "本科及以上",
        "description": (
            "负责核心业务服务的 Python 后端开发，使用 FastAPI 构建接口，"
            "PostgreSQL 与 Redis 做存储与缓存，Celery 处理异步任务；"
            "要求熟悉 pytest 单元测试与性能优化，有高并发服务经验优先。"
        ),
    },
    {
        "title": "资深 Python 服务端工程师（平台方向）",
        "role_family": "backend_python",
        "salary": "30-50K·15薪",
        "experience": "5-8年",
        "education": "本科及以上",
        "description": (
            "负责平台层服务架构演进：微服务拆分、消息队列削峰、分库分表；"
            "技术栈 Python / FastAPI / Kafka / PostgreSQL / Redis / Docker / Kubernetes，"
            "要求有高并发与性能优化实战经验，熟悉 CI 与质量门禁建设。"
        ),
    },
    {
        "title": "Python 后端工程师（业务中台）",
        "role_family": "backend_python",
        "salary": "22-35K·13薪",
        "experience": "3-5年",
        "education": "本科及以上",
        "description": (
            "参与业务中台服务开发与维护：Python、Django、MySQL、Redis、Celery；"
            "编写 pytest 自动化测试，参与接口性能优化与 SQL 调优。"
        ),
    },
    # ---- ai_application ----
    {
        "title": "大模型应用开发工程师",
        "role_family": "ai_application",
        "salary": "35-60K·15薪",
        "experience": "3-5年",
        "education": "本科及以上",
        "description": (
            "负责大模型（LLM）应用落地：RAG 检索增强、LangChain 编排、"
            "向量数据库检索与 rerank、提示工程与 Agent 工具调用；"
            "要求 Python 工程能力扎实，有 FastAPI 服务化经验优先。"
        ),
    },
    {
        "title": "AI应用工程师（RAG 方向）",
        "role_family": "ai_application",
        "salary": "30-55K·14薪",
        "experience": "3-5年",
        "education": "本科及以上",
        "description": (
            "建设企业知识库问答：RAG 流水线、向量数据库、bm25 与语义混合检索、"
            "提示工程与评测体系；技术栈 Python / LLM / LangChain / PostgreSQL。"
        ),
    },
    # ---- data ----
    {
        "title": "数据开发工程师",
        "role_family": "data",
        "salary": "25-45K·14薪",
        "experience": "3-5年",
        "education": "本科及以上",
        "description": (
            "负责数仓建设与 ETL 流水线开发：Spark、Flink、Hive、SQL 调优；"
            "要求熟悉 Python，有大数据平台开发经验。"
        ),
    },
    {
        "title": "数据分析师（增长方向）",
        "role_family": "data",
        "salary": "20-35K·13薪",
        "experience": "3-5年",
        "education": "本科及以上",
        "description": (
            "负责增长业务的数据分析：SQL 取数与建模、AB 实验设计与归因分析、"
            "指标体系与报表建设；要求熟练使用 Python 做数据分析。"
        ),
    },
    # ---- frontend ----
    {
        "title": "前端开发工程师（React）",
        "role_family": "frontend",
        "salary": "22-38K·14薪",
        "experience": "3-5年",
        "education": "本科及以上",
        "description": (
            "负责 Web 前端开发：React、TypeScript、Next.js、Vite，"
            "组件库建设与性能优化；要求熟悉 CI 流程与前端工程化。"
        ),
    },
    {
        "title": "前端开发工程师（Vue3）",
        "role_family": "frontend",
        "salary": "20-35K·13薪",
        "experience": "3-5年",
        "education": "本科及以上",
        "description": (
            "负责业务前端与小程序开发：Vue3、TypeScript、Vite、Webpack、组件库；"
            "参与前端性能优化与工程化建设。"
        ),
    },
)

# 虚构公司名池：按（城市 × 模板）序号轮转，保证「公司+标准职位+城市」唯一，
# 不触发跨岗位去重合并。全部为虚构名称。
_COMPANY_POOL: tuple[str, ...] = (
    "青梧云计算有限公司",
    "岚岳智能科技有限公司",
    "汐潮网络科技有限公司",
    "白泽数据服务有限公司",
    "栖云互动科技有限公司",
    "澈海信息技术有限公司",
    "松屿软件技术有限公司",
    "曜石科技集团",
    "沅芷智算科技有限公司",
    "叠翠网络技术有限公司",
)

# 固定发布时间：内容完全确定，重复 seed 不产生“内容变更”噪声
_PUBLISHED_AT: tuple[str, ...] = (
    "2026-07-21T09:00:00+08:00",
    "2026-07-23T10:30:00+08:00",
    "2026-07-26T14:00:00+08:00",
)

SEED_SOURCE_JOB_PREFIX = "seed"


def build_seed_job_payloads() -> list[tuple[str, SeedJobPayload]]:
    """生成 (source_job_id, payload) 列表：3 城市 × 9 模板 + 1 补充 = 28 个。

    source_job_id 稳定（seed:001..seed:028），重复执行走管道 upsert，不产生重复数据。
    """
    items: list[tuple[str, SeedJobPayload]] = []
    index = 0
    for city_i, city in enumerate(SEED_CITY_NAMES):
        for spec_i, spec in enumerate(_JOB_SPECS):
            index += 1
            company = _COMPANY_POOL[(city_i * len(_JOB_SPECS) + spec_i) % len(_COMPANY_POOL)]
            payload: SeedJobPayload = {
                "title": spec["title"],
                "company": company,
                "city": city,
                "salary": spec["salary"],
                "experience": spec["experience"],
                "education": spec["education"],
                "employment": "全职",
                "description": spec["description"] + _SYNTHETIC_NOTE,
                "published_at": _PUBLISHED_AT[index % len(_PUBLISHED_AT)],
            }
            items.append((f"{SEED_SOURCE_JOB_PREFIX}:{index:03d}", payload))
    # 第 28 个：补一个北京的 Python 后端岗（换公司避免与 seed:001 去重合并）
    index += 1
    payload_extra: SeedJobPayload = {
        "title": "Python 后端开发工程师（基础架构）",
        "company": "曜石科技集团",
        "city": "北京",
        "salary": "28-45K·15薪",
        "experience": "3-5年",
        "education": "本科及以上",
        "employment": "全职",
        "description": (
            "负责基础架构方向 Python 服务开发：FastAPI、asyncio、Redis、Kafka、"
            "PostgreSQL，高并发链路性能优化与可观测性建设。" + _SYNTHETIC_NOTE
        ),
        "published_at": _PUBLISHED_AT[index % len(_PUBLISHED_AT)],
    }
    items.append((f"{SEED_SOURCE_JOB_PREFIX}:{index:03d}", payload_extra))
    return items


def expected_role_family(source_job_id: str) -> str:
    """测试辅助：种子岗位应归入的方向（seed:028 为补充的 backend_python）。"""
    seq = int(source_job_id.split(":")[1])
    if seq == 28:
        return "backend_python"
    spec = _JOB_SPECS[(seq - 1) % len(_JOB_SPECS)]
    return spec["role_family"]
