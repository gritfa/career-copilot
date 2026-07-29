"""Fixture 企业官网纵向连接器 A/B（ADR-001 D1）。

现实约束：无法完成真实企业官网的访问政策调查（robots/条款/允许频率），
按 00-master 硬边界不得抓取，能力一律如实标 ``not_verified``。
本连接器数据全部来自代码内合成岗位（公司与岗位均为虚构），
但走与真实连接器完全相同的 discover → snapshot → normalize → dedupe 管道，
用于验证管道、限速、熔断与去重逻辑。真实连接器接入时只需替换数据获取层。
"""

import json
from datetime import UTC, datetime, timedelta

from app.jobs.adapters.base import (
    DiscoveryPage,
    RawJobSnapshot,
    SourceJobRef,
    SourcePolicy,
    ValidityResult,
)
from app.jobs.constants import SOURCE_KEY_FIXTURE_A, SOURCE_KEY_FIXTURE_B

_PAGE_SIZE = 4


def _job(
    job_id: str,
    title: str,
    city: str,
    salary: str,
    experience: str,
    education: str,
    description: str,
    *,
    employment: str = "全职",
    validity: str = "active",
    published_days_ago: int = 3,
) -> dict:
    return {
        "source_job_id": job_id,
        "title": title,
        "city": city,
        "salary": salary,
        "experience": experience,
        "education": education,
        "employment": employment,
        "description": description,
        "validity": validity,
        "published_days_ago": published_days_ago,
    }


# 合成岗位：六方向 × 六城市覆盖 + 标准化边界分支（面议/日薪/年薪/13薪/
# 学历 preferred/无法映射城市/外包驻场/非全职）
_FIXTURE_A_COMPANY = "星岚科技"
_FIXTURE_A_DOMAIN = "careers.xinglan-fixture.example"
_FIXTURE_A_JOBS: list[dict] = [
    _job(
        "XL-001",
        "大模型应用工程师",
        "北京",
        "30-50K·14薪",
        "3-5年",
        "本科及以上",
        "负责 LLM 应用与 Agent 编排，落地 RAG 检索增强与工具调用链路，"
        "要求熟悉 Python、LangChain/LangGraph、向量数据库与提示工程。",
    ),
    _job(
        "XL-002",
        "Python 后端开发工程师",
        "杭州",
        "20-35K·13薪",
        "1-3年",
        "本科及以上",
        "负责核心交易服务的 FastAPI/异步微服务开发，PostgreSQL 与 Redis 调优，"
        "参与服务可观测性与稳定性建设。",
    ),
    _job(
        "XL-003",
        "Java 后端开发工程师",
        "深圳",
        "25-40K",
        "3-5年",
        "本科及以上",
        "负责订单中台 Spring Boot/Spring Cloud 服务开发，分库分表与消息队列，"
        "高并发场景性能优化。",
    ),
    _job(
        "XL-004",
        "前端开发工程师",
        "上海",
        "18-30K·13薪",
        "1-3年",
        "大专及以上",
        "负责 React/TypeScript 中后台前端开发，组件库建设与工程化，"
        "熟悉 Vite/Next.js 优先。",
    ),
    _job(
        "XL-005",
        "数据开发工程师",
        "成都",
        "15-28K",
        "1-3年",
        "本科及以上",
        "负责数仓 ETL 管道建设，Spark/Flink 实时离线一体化，指标体系与数据质量监控。",
    ),
    _job(
        "XL-006",
        "测试开发工程师",
        "广州",
        "面议",
        "经验不限",
        "本科优先",
        "负责自动化测试平台建设，接口/UI 自动化框架，CI 流水线质量门禁。薪资面议。",
    ),
    _job(
        "XL-007",
        "驻场测试工程师（外包项目）",
        "深圳",
        "12-18K",
        "1-3年",
        "大专及以上",
        "人力外包项目，需长期驻场客户现场工作，负责功能测试与回归测试，接受派遣。",
    ),
    _job(
        "XL-008",
        "资深售前顾问",
        "武汉",
        "20-35万/年",
        "5-10年",
        "硕士及以上",
        "负责大客户售前方案与投标支持，行业解决方案输出。",
    ),
    _job(
        "XL-009",
        "前端开发实习生",
        "北京",
        "300-450元/天",
        "应届",
        "本科及以上",
        "实习岗位：参与 Vue3 业务组件开发，可转正。",
        employment="实习",
    ),
    _job(
        "XL-010",
        "Python 后端开发工程师（已下架）",
        "北京",
        "20-30K",
        "1-3年",
        "本科及以上",
        "该岗位已停止招聘，用于验证有效性三态检查。",
        validity="inactive",
        published_days_ago=40,
    ),
]

_FIXTURE_B_COMPANY = "云图智能"
_FIXTURE_B_DOMAIN = "jobs.yuntu-fixture.example"
_FIXTURE_B_JOBS: list[dict] = [
    _job(
        "YT-101",
        "AIGC 应用开发工程师",
        "上海",
        "25-45K·16薪",
        "1-3年",
        "本科及以上",
        "负责多模态生成应用与 Agent 工作流开发，提示工程与模型评测，"
        "要求熟悉 Python 与大模型 API 生态。",
    ),
    _job(
        "YT-102",
        "Java 开发工程师",
        "北京",
        "22-38K·13薪",
        "3-5年",
        "本科及以上",
        "负责营销系统 Java 微服务开发，Spring Cloud Alibaba 与分布式事务。",
    ),
    _job(
        "YT-103",
        "数据分析师",
        "杭州",
        "15-25K",
        "1-3年",
        "本科优先",
        "负责业务数据分析与看板建设，SQL/Python 数据处理，AB 实验设计与归因分析。",
    ),
    _job(
        "YT-104",
        "前端开发工程师（远程）",
        "远程",
        "20-32K",
        "3-5年",
        "学历不限",
        "远程办公岗位：负责 Web 前端架构与性能优化，React + TypeScript 技术栈。",
    ),
    _job(
        "YT-105",
        "软件测试工程师",
        "成都",
        "12-20K·13薪",
        "1-3年",
        "大专及以上",
        "负责金融业务功能测试与接口自动化，pytest + requests 框架。",
    ),
    _job(
        "YT-106",
        "Python 后端开发工程师",
        "广州",
        "18-30K",
        "1-3年",
        "本科及以上",
        "负责内容平台后端 Django/FastAPI 服务开发与性能优化，参与架构演进。",
    ),
    _job(
        "YT-107",
        "大数据开发工程师（外包派遣）",
        "深圳",
        "面议",
        "3-5年",
        "本科及以上",
        "第三方派遣岗位，外派至甲方数据团队，负责 Hive/Spark 离线数仓开发。",
    ),
]


class FixtureCompanySiteAdapter:
    """合成企业官网连接器：分页 discover / 详情 / 有效性三态。"""

    def __init__(
        self,
        source_key: str,
        company_name: str,
        domain: str,
        jobs: list[dict],
    ) -> None:
        self.source_key = source_key
        self.company_name = company_name
        self.domain = domain
        self._jobs = {job["source_job_id"]: job for job in jobs}

    # ---- 内部 ----

    def _url(self, job_id: str) -> str:
        return f"https://{self.domain}/jobs/{job_id}"

    def _ref(self, job_id: str) -> SourceJobRef:
        return SourceJobRef(
            source_key=self.source_key, source_job_id=job_id, url=self._url(job_id)
        )

    # ---- JobSourceAdapter ----

    async def discover(self, cursor: str | None) -> DiscoveryPage:
        job_ids = sorted(self._jobs)
        start = int(cursor) if cursor else 0
        page_ids = job_ids[start : start + _PAGE_SIZE]
        next_start = start + _PAGE_SIZE
        next_cursor = str(next_start) if next_start < len(job_ids) else None
        return DiscoveryPage(refs=[self._ref(j) for j in page_ids], next_cursor=next_cursor)

    async def fetch_detail(self, ref: SourceJobRef) -> RawJobSnapshot:
        job = self._jobs.get(ref.source_job_id)
        if job is None:
            raise KeyError(f"unknown fixture job: {ref.source_job_id}")
        now = datetime.now(UTC)
        payload = {
            **{k: v for k, v in job.items() if k not in ("validity", "published_days_ago")},
            "company": self.company_name,
            "url": ref.url,
            "published_at": (
                now - timedelta(days=job["published_days_ago"])
            ).isoformat(),
        }
        return RawJobSnapshot(
            ref=ref,
            content=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            media_type="application/json",
            fetched_at=now,
            http_meta={"status": 200, "fixture": True},
        )

    async def check_validity(self, ref: SourceJobRef) -> ValidityResult:
        now = datetime.now(UTC)
        job = self._jobs.get(ref.source_job_id)
        if job is None:
            return ValidityResult(status="inactive", checked_at=now, reason="NOT_FOUND")
        if job["validity"] == "inactive":
            return ValidityResult(status="inactive", checked_at=now, reason="CLOSED")
        if job["validity"] == "unknown":
            return ValidityResult(status="unknown", checked_at=now, reason="TEMPORARY_ERROR")
        return ValidityResult(status="active", checked_at=now)

    def policy(self) -> SourcePolicy:
        return SourcePolicy(
            source_key=self.source_key,
            source_type="company_site",
            allowed_modes=("fixture",),
            # 未完成真实来源政策调查，不得标 verified（00-master 硬边界）
            policy_status="not_verified",
            access_policy_url=None,
            rate_limit_per_minute=30,
            notes="合成 fixture 数据；真实企业官网政策调查完成前禁止抓取。",
        )


def fixture_adapter_a() -> FixtureCompanySiteAdapter:
    return FixtureCompanySiteAdapter(
        SOURCE_KEY_FIXTURE_A, _FIXTURE_A_COMPANY, _FIXTURE_A_DOMAIN, _FIXTURE_A_JOBS
    )


def fixture_adapter_b() -> FixtureCompanySiteAdapter:
    return FixtureCompanySiteAdapter(
        SOURCE_KEY_FIXTURE_B, _FIXTURE_B_COMPANY, _FIXTURE_B_DOMAIN, _FIXTURE_B_JOBS
    )
