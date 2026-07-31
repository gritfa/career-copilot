"""每日推荐 Celery 任务：硬过滤 → 向量召回 → 综合评分 → 每日前 20 新岗位。

不变量：
- failed 岗位绝不入推荐；unknown（如薪资面议）以 uncertain 状态进入。
- 同方案+岗位+日期唯一（DB 约束）；历史已推荐过的岗位不重复推荐。
- 方案 minimum_match_score 过滤；每日新增上限 20。
- Embedding 失败降级：使用已有向量继续，新内容暂不进语义召回（docs/07 第 12 节）。
- 日志只含 ID / 计数 / 版本，绝不含岗位正文、简历事实内容。
"""

import uuid
from datetime import UTC, date, datetime

import structlog
from sqlalchemy import Engine, and_, create_engine, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.models import (
    CanonicalJob,
    Company,
    CompanyPreference,
    JobPosting,
    LearnedPreference,
    MatchComponent,
    ProfileFact,
    Recommendation,
    SearchPlan,
    UsageLedger,
)
from app.integrations.embedding import (
    EmbeddingError,
    EmbeddingGateway,
    EmbeddingUsage,
)
from app.matching.constants import HARD_RULE_VERSION, SCORING_VERSION
from app.matching.hard_filters import (
    PlanContext,
    evaluate_hard_conditions,
    summarize_hard_status,
)
from app.matching.profile import build_user_profile
from app.matching.recall import recall_top_k, upsert_job_vector, upsert_profile_vector
from app.matching.scoring import ScoringResult, compute_score
from app.matching.vector_text import (
    PREPROCESS_VERSION,
    build_job_vector_text,
    build_profile_vector_text,
)
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("app.matching.tasks")


def _task_session() -> tuple[Session, Engine]:
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    return Session(engine), engine


def _record_usage(db: Session, user_id: uuid.UUID | None, usage: EmbeddingUsage | None) -> None:
    if usage is None:
        return
    db.add(
        UsageLedger(
            user_id=user_id,
            provider=usage.provider,
            model=usage.model_id,
            operation_type="embedding",
            tokens_in=usage.tokens_estimated,
            tokens_out=0,
            amount_estimated=usage.amount_estimated,
        )
    )


def _primary_posting(db: Session, job: CanonicalJob) -> JobPosting | None:
    if job.primary_posting_id is not None:
        posting = db.get(JobPosting, job.primary_posting_id)
        if posting is not None:
            return posting
    return db.execute(
        select(JobPosting)
        .where(JobPosting.canonical_job_id == job.id)
        .order_by(JobPosting.first_seen_at)
        .limit(1)
    ).scalar_one_or_none()


@celery_app.task(name="matching.generate_recommendations", bind=True, max_retries=0)
def generate_recommendations_task(self, plan_id: str, run_date: str | None = None) -> dict:
    """为单个求职方案生成当日推荐；幂等（同日重复执行只补新岗位）。"""
    settings = get_settings()
    day = date.fromisoformat(run_date) if run_date else datetime.now(UTC).date()
    db, engine = _task_session()
    try:
        plan = db.get(SearchPlan, uuid.UUID(plan_id))
        if plan is None:
            return {"status": "plan_not_found"}
        if plan.status != "active":
            return {"status": f"skipped:{plan.status}"}

        # ---- 用户画像（只用已确认事实） ----
        facts = list(
            db.execute(
                select(ProfileFact).where(
                    ProfileFact.user_id == plan.user_id, ProfileFact.status == "active"
                )
            )
            .scalars()
            .all()
        )
        profile = build_user_profile(facts)

        # ---- 公司偏好（block 高于一切） ----
        pref_rows = db.execute(
            select(CompanyPreference, Company)
            .join(Company, Company.id == CompanyPreference.company_id)
            .where(CompanyPreference.search_plan_id == plan.id)
        ).all()
        blocked_ids = {c.id for p, c in pref_rows if p.preference == "block"}
        blocked_names = {c.id: c.canonical_name for p, c in pref_rows if p.preference == "block"}
        company_prefs = {c.id: p.preference for p, c in pref_rows if p.preference != "block"}

        learned = db.execute(
            select(LearnedPreference).where(
                LearnedPreference.user_id == plan.user_id,
                LearnedPreference.status == "active",
            )
        ).scalar_one_or_none()
        learned_weights = dict(learned.weights_json) if learned is not None else {}

        plan_ctx = PlanContext(
            city_codes=list(plan.city_codes),
            work_modes=list(plan.work_modes),
            minimum_monthly_salary=plan.minimum_monthly_salary,
            allow_outsourcing=plan.allow_outsourcing,
            blocked_company_ids=blocked_ids,
            blocked_company_names=blocked_names,
            user_education_rank=profile.education_rank,
            user_education_level=profile.education_level,
            user_years_experience=profile.years_experience,
        )

        # ---- 候选池：active canonical jobs + 主 posting ----
        # 可见性隔离（阶段 10 任务 B）：公开岗位 + 本人私有导入岗位；
        # 他人私有岗位、无主私有岗位（owner=NULL）绝不进入候选池。
        jobs = list(
            db.execute(
                select(CanonicalJob).where(
                    CanonicalJob.status == "active",
                    or_(
                        CanonicalJob.visibility == "global",
                        and_(
                            CanonicalJob.visibility == "private",
                            CanonicalJob.owner_user_id == plan.user_id,
                        ),
                    ),
                )
            )
            .scalars()
            .all()
        )
        evaluated = 0
        survivors: dict[uuid.UUID, dict] = {}  # canonical_job_id -> {posting, hard, status}
        for job in jobs:
            posting = _primary_posting(db, job)
            if posting is None:
                continue
            evaluated += 1
            hard_results = evaluate_hard_conditions(plan_ctx, posting)
            status = summarize_hard_status(hard_results)
            if status == "failed":
                continue
            survivors[job.id] = {"posting": posting, "hard": hard_results, "status": status}

        # ---- 向量：profile + 幸存岗位；失败降级（用已有向量继续） ----
        gateway = EmbeddingGateway()
        similarity: dict[uuid.UUID, float] = {}
        recall_ids = list(survivors.keys())
        degraded = False
        try:
            profile_text = build_profile_vector_text(
                role_family=plan.role_family,
                city_codes=list(plan.city_codes),
                work_modes=list(plan.work_modes),
                facts=facts,
            )
            pvec, usage = upsert_profile_vector(db, gateway, plan.id, profile_text)
            _record_usage(db, plan.user_id, usage)
            for job_id, item in survivors.items():
                jvec_text = build_job_vector_text(item["posting"])
                try:
                    _, j_usage = upsert_job_vector(db, gateway, job_id, jvec_text)
                    _record_usage(db, plan.user_id, j_usage)
                except EmbeddingError:
                    degraded = True  # 单岗失败：暂不进语义召回，不中断整体
            hits = recall_top_k(
                db,
                pvec.embedding,
                model_id=gateway.adapter.model_id,
                preprocess_version=PREPROCESS_VERSION,
                candidate_ids=recall_ids,
                k=settings.recall_top_k,
            )
            similarity = {h.canonical_job_id: h.similarity for h in hits}
            scored_ids = [h.canonical_job_id for h in hits]
        except EmbeddingError:
            # 整体降级：无向量召回，按硬过滤幸存集合直接评分（规则基础结果）
            degraded = True
            logger.warning("embedding_degraded", plan_id=plan_id)
            scored_ids = recall_ids

        # ---- 去重：历史已推荐过的岗位（任何日期）不再推荐 ----
        already = set(
            db.execute(
                select(Recommendation.canonical_job_id).where(
                    Recommendation.search_plan_id == plan.id
                )
            )
            .scalars()
            .all()
        )
        today_count = db.execute(
            select(Recommendation).where(
                Recommendation.search_plan_id == plan.id,
                Recommendation.recommended_on == day,
            )
        ).scalars().all()
        remaining_quota = max(0, settings.daily_recommendation_limit - len(today_count))

        # ---- 评分 ----
        scored: list[tuple[int, uuid.UUID, ScoringResult]] = []
        for job_id in scored_ids:
            if job_id in already:
                continue
            item = survivors[job_id]
            posting = item["posting"]
            result = compute_score(
                profile=profile,
                plan=plan,
                posting=posting,
                hard_results=item["hard"],
                cosine_similarity=similarity.get(job_id),
                company_preference=company_prefs.get(posting.company_id),
                learned_weights=learned_weights,
            )
            if result.score_total < plan.minimum_match_score:
                continue
            scored.append((result.score_total, job_id, result))

        scored.sort(key=lambda t: (-t[0], str(t[1])))
        created = 0
        base_rank = len(today_count)
        for score_total, job_id, result in scored[:remaining_quota]:
            item = survivors[job_id]
            rec = Recommendation(
                id=uuid.uuid4(),
                search_plan_id=plan.id,
                canonical_job_id=job_id,
                score_total=score_total,
                grade=result.grade,
                hard_filter_status=item["status"],
                hard_conditions_json=[r.as_dict() for r in item["hard"]],
                scoring_version=SCORING_VERSION,
                versions_json={
                    "hard_rule_version": HARD_RULE_VERSION,
                    "embedding_model_id": gateway.adapter.model_id,
                    "preprocess_version": PREPROCESS_VERSION,
                    "embedding_degraded": degraded,
                },
                rank=base_rank + created + 1,
                recommended_on=day,
            )
            db.add(rec)
            try:
                # 先落推荐行（无 relationship，需显式保证 FK 先后顺序）
                db.flush()
            except IntegrityError:
                # 并发同日重复：唯一约束兜底，跳过该岗位
                db.rollback()
                continue
            for comp in result.components:
                db.add(
                    MatchComponent(
                        id=uuid.uuid4(),
                        recommendation_id=rec.id,
                        component=comp.component,
                        score=comp.score,
                        weight=comp.weight,
                        evidence_refs_json=comp.evidence_refs,
                        gap_level=comp.gap_level,
                        uncertainty=comp.uncertainty,
                    )
                )
            db.flush()
            created += 1

        db.commit()
        stats = {
            "status": "ok",
            "plan_id": plan_id,
            "day": day.isoformat(),
            "evaluated": evaluated,
            "hard_survivors": len(survivors),
            "created": created,
            "degraded": degraded,
        }
        logger.info("recommendations_generated", **stats)
        return stats
    finally:
        db.close()
        engine.dispose()


@celery_app.task(name="matching.generate_all_recommendations")
def generate_all_recommendations_task() -> dict[str, str]:
    """每日为全部 active 方案生成推荐（beat 注册在岗位同步之后）。"""
    db, engine = _task_session()
    try:
        plan_ids = [
            str(pid)
            for pid in db.execute(
                select(SearchPlan.id).where(SearchPlan.status == "active")
            ).scalars()
        ]
    finally:
        db.close()
        engine.dispose()

    results: dict[str, str] = {}
    eager = get_settings().celery_task_always_eager
    for plan_id in plan_ids:
        if eager:
            outcome = generate_recommendations_task.apply(args=(plan_id,)).get()
            results[plan_id] = outcome.get("status", "unknown")
        else:
            generate_recommendations_task.delay(plan_id)
            results[plan_id] = "scheduled"
    return results
