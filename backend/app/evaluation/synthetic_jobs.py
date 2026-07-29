"""合成岗位目录（离线评测专用，ADR-001 D5 / docs/09 3.1）。

- **全部岗位为虚构合成数据**：公司名带「合成样例」前缀，未从任何招聘平台
  或企业官网抓取；仅用于离线评测基线，绝不入生产库。
- 结构与 ``JobPosting`` 同构（duck-typing）：硬条件引擎与评分引擎按属性访问，
  这里提供其全部所需字段。
- 覆盖硬条件全部分支：城市不匹配、薪资面议（unknown）、薪资过低（failed）、
  非全职、外包高置信信号、学历 required/preferred、经验 range/unrestricted。
"""

from dataclasses import dataclass, field
from typing import Any

from app.jobs.constants import CITY_CODE_BY_NAME


@dataclass(frozen=True)
class SyntheticPosting:
    """与 JobPosting 同构的合成岗位（company_id=None → 不触发屏蔽公司条件）。"""

    job_id: str
    role_family: str
    title_raw: str
    description_text: str
    city_kind: str  # city / nationwide / remote
    city_code: str | None
    employment_type: str | None  # full_time / other / None(未标注)
    salary_min: int | None
    salary_max: int | None
    salary_raw: str | None
    salary_unknown: bool
    education_requirement_type: str | None  # required / preferred / None
    education_level: str | None
    experience_type: str  # range / unrestricted / fresh_grad / unknown
    experience_min: float | None
    experience_max: float | None
    work_mode: str | None = None
    outsourcing_signals: list[dict[str, Any]] = field(default_factory=list)
    title_normalized: str | None = None
    company_id: None = None


_C = CITY_CODE_BY_NAME

# 六方向 JD 正文：确定性词面评分依赖 TECH_TERMS 词表，正文覆盖典型技术栈
_JD = {
    "ai_application": (
        "岗位职责：负责企业级 LLM 应用与 RAG 检索链路的设计与落地，"
        "包括文档解析、混合检索（BM25 + 向量数据库/pgvector/Milvus）、rerank 与引用标注；"
        "建设 prompt 工程与 function calling / 工具调用规范；搭建离线评测与灰度对比。"
        "任职要求：熟练 Python、FastAPI、asyncio；熟悉 LangChain 或 LangGraph、Agent 编排；"
        "熟悉 Redis、PostgreSQL、Docker；关注大模型 Token 成本与延迟优化。"
    ),
    "backend_python": (
        "岗位职责：负责核心业务系统的 Python 后端研发，参与微服务拆分与高并发场景性能优化；"
        "建设异步任务与消息队列链路。任职要求：熟练 Python、FastAPI 或 Django、Celery；"
        "熟悉 MySQL、PostgreSQL、Redis、Kafka；熟悉 Docker、Kubernetes 与 GitLab CI；"
        "有 SQL 调优与分库分表经验优先。"
    ),
    "backend_java": (
        "岗位职责：负责交易与订单域 Java 后端研发，参与微服务架构演进与高并发性能优化。"
        "任职要求：熟练 Java、Spring Boot、Spring Cloud、MyBatis；"
        "熟悉 MySQL、Redis、Kafka、RabbitMQ 等消息队列；熟悉分库分表；"
        "熟悉 Docker、Kubernetes 部署。"
    ),
    "frontend": (
        "岗位职责：负责核心产品 Web 前端与小程序研发，建设组件库与工程化体系，"
        "推进首屏性能优化。任职要求：熟练 TypeScript、JavaScript；精通 Vue3 或 React；"
        "熟悉 Vite、Webpack 构建；有组件库或微前端建设经验优先。"
    ),
    "data": (
        "岗位职责：负责数仓建模与 ETL 链路建设，支撑经营分析与 AB 实验/归因分析。"
        "任职要求：熟练 SQL、Python；熟悉 Hive、Spark、Flink；"
        "有数据仓库分层建设与数据分析报表经验；熟悉 Kafka 数据接入。"
    ),
    "qa": (
        "岗位职责：负责核心业务的测试开发，建设接口自动化与 UI 自动化体系，"
        "完善质量门禁与回归测试。任职要求：熟练 Python、pytest、requests；"
        "熟悉 Selenium、Appium、JMeter；有接口自动化框架建设经验；熟悉功能测试用例设计。"
    ),
}

# 各方向 A 岗所在城市（对齐该方向首份合成简历所在城市）
_HOME_CITY = {
    "ai_application": "北京",
    "backend_python": "上海",
    "backend_java": "广州",
    "frontend": "深圳",
    "data": "上海",
    "qa": "成都",
}

_TITLES = {
    "ai_application": "AI 应用开发工程师（LLM/RAG）",
    "backend_python": "Python 后端开发工程师",
    "backend_java": "Java 后端开发工程师",
    "frontend": "前端开发工程师",
    "data": "数据开发工程师（数仓方向）",
    "qa": "测试开发工程师",
}


def _family_jobs(family: str) -> list[SyntheticPosting]:
    city = _HOME_CITY[family]
    title = _TITLES[family]
    jd = _JD[family]
    return [
        # A：本方向主力岗——指定城市、明码薪资、本科硬性要求、3-5 年经验
        SyntheticPosting(
            job_id=f"{family}-A",
            role_family=family,
            title_raw=f"{title}（合成样例）",
            description_text=jd + " 学历要求：统招本科及以上。经验要求：3-5 年。",
            city_kind="city",
            city_code=_C[city],
            employment_type="full_time",
            salary_min=20000,
            salary_max=35000,
            salary_raw="20-35K",
            salary_unknown=False,
            education_requirement_type="required",
            education_level="bachelor",
            experience_type="range",
            experience_min=3,
            experience_max=5,
        ),
        # B：全国/多地岗——薪资面议（unknown 分支）、硕士优先（preferred 降分分支）
        SyntheticPosting(
            job_id=f"{family}-B",
            role_family=family,
            title_raw=f"资深{title}（多地招聘·合成样例）",
            description_text=jd + " 硕士学历优先。多地招聘，base 可协商。",
            city_kind="nationwide",
            city_code=None,
            employment_type="full_time",
            salary_min=None,
            salary_max=None,
            salary_raw="面议",
            salary_unknown=True,
            education_requirement_type="preferred",
            education_level="master",
            experience_type="unrestricted",
            experience_min=None,
            experience_max=None,
            work_mode="onsite",
        ),
        # C：外包高置信 + 薪资低于方案下限（双重 failed 分支）
        SyntheticPosting(
            job_id=f"{family}-C",
            role_family=family,
            title_raw=f"{title}（外包驻场·合成样例）",
            description_text=jd + " 外包驻场项目，接受出差。",
            city_kind="nationwide",
            city_code=None,
            employment_type="full_time",
            salary_min=8000,
            salary_max=11000,
            salary_raw="8-11K",
            salary_unknown=False,
            education_requirement_type=None,
            education_level=None,
            experience_type="range",
            experience_min=1,
            experience_max=3,
            outsourcing_signals=[
                {"confidence": 0.9, "evidence": "标题命中：外包驻场"},
            ],
        ),
    ]


def build_synthetic_jobs() -> list[SyntheticPosting]:
    """构造完整合成岗位目录：6 方向 × 3 + 2 个方向未知岗 = 20 个。"""
    jobs: list[SyntheticPosting] = []
    for family in _HOME_CITY:
        jobs.extend(_family_jobs(family))
    jobs.append(
        # X1：非全职（full_time failed 分支）+ 方向未知
        SyntheticPosting(
            job_id="unknown-X1",
            role_family="unknown",
            title_raw="技术实习生（合成样例）",
            description_text="参与内部工具开发与测试，方向不限。实习岗位，每周到岗 4 天。",
            city_kind="nationwide",
            city_code=None,
            employment_type="other",
            salary_min=4000,
            salary_max=6000,
            salary_raw="4-6K",
            salary_unknown=False,
            education_requirement_type=None,
            education_level=None,
            experience_type="unrestricted",
            experience_min=None,
            experience_max=None,
        )
    )
    jobs.append(
        # X2：方向未知的通过岗——role_semantic 走向量余弦相似度分支
        SyntheticPosting(
            job_id="unknown-X2",
            role_family="unknown",
            title_raw="研发工程师（方向不限·合成样例）",
            description_text=(
                "岗位职责：参与内部平台研发，方向可为后端、前端、数据或测试。"
                "任职要求：具备扎实的编程基础，熟悉 Git 协作；技术栈不限。"
            ),
            city_kind="nationwide",
            city_code=None,
            employment_type="full_time",
            salary_min=15000,
            salary_max=25000,
            salary_raw="15-25K",
            salary_unknown=False,
            education_requirement_type=None,
            education_level=None,
            experience_type="unrestricted",
            experience_min=None,
            experience_max=None,
            work_mode="onsite",
        )
    )
    return jobs


SYNTHETIC_JOBS: tuple[SyntheticPosting, ...] = tuple(build_synthetic_jobs())
