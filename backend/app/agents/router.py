"""标准分析 API（docs/04 第 7 节，ADR-001 裁剪版）。

- POST /recommendations/{id}/deep-analysis：手动触发，校验每日额度（3 次/账号）；
  同一推荐已有进行中的任务时幂等返回该任务，不重复扣额度。
- GET /agent-runs/{id}：只返回总体状态与最终结构化报告，
  绝不返回内部对话/提示词/思维链。
- GET /recommendations/{id}/analyses：该推荐的分析任务列表（新→旧）。
- 越权一律 404（防 IDOR，不泄露存在性）。
- SSE 事件流按 ADR-001 推迟，前端轮询 GET /agent-runs/{id}。
"""

import uuid
from datetime import UTC, datetime
from datetime import time as dtime

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.schemas import (
    GRAPH_VERSION,
    OUTPUT_SCHEMA_VERSION,
    AgentRunListOut,
    AgentRunOut,
    StandardAnalysisReport,
)
from app.agents.service import provider_verified
from app.agents.tasks import run_standard_analysis_task
from app.auth.deps import AuthContext, require_active_user, require_user
from app.core.errors import AppError
from app.core.quotas import effective_limit
from app.db.models import AgentRun, Recommendation, SearchPlan
from app.db.session import get_db
from app.integrations.llm_gateway import get_llm_adapter
from app.tasks.celery_app import dispatch_task

router = APIRouter(tags=["analysis"])

_NOT_FOUND = AppError(code="NOT_FOUND", message="资源不存在", status_code=404)

_ACTIVE_STATUSES = ("queued", "analyzing", "validating")


def _run_out(run: AgentRun) -> AgentRunOut:
    report = None
    if run.final_report_json is not None:
        report = StandardAnalysisReport.model_validate(run.final_report_json)
    return AgentRunOut(
        id=run.id,
        recommendation_id=run.recommendation_id,
        status=run.status,
        trigger=run.trigger,
        provider=run.provider,
        model_id=(run.model_map_json or {}).get("standard_analysis"),
        verified=provider_verified(run.provider),
        graph_version=run.graph_version,
        output_schema_version=run.output_schema_version,
        error_code=run.error_code,
        created_at=run.created_at,
        completed_at=run.completed_at,
        report=report,
    )


async def _get_owned_recommendation(
    db: AsyncSession, rec_id: uuid.UUID, ctx: AuthContext
) -> Recommendation:
    row = (
        await db.execute(
            select(Recommendation, SearchPlan)
            .join(SearchPlan, SearchPlan.id == Recommendation.search_plan_id)
            .where(Recommendation.id == rec_id)
        )
    ).first()
    if row is None or row[1].user_id != ctx.user.id:
        raise _NOT_FOUND
    return row[0]


@router.post(
    "/recommendations/{rec_id}/deep-analysis",
    response_model=AgentRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_analysis(
    rec_id: uuid.UUID,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> AgentRunOut:
    """手动触发标准分析（每账号每日 3 次；进行中的任务幂等复用）。"""
    rec = await _get_owned_recommendation(db, rec_id, ctx)

    # 同一推荐已有进行中的任务：幂等返回，不重复扣额度
    active = (
        await db.execute(
            select(AgentRun)
            .where(
                AgentRun.recommendation_id == rec.id,
                AgentRun.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if active is not None:
        return _run_out(active)

    # 每日手动额度（UTC 日界，docs/07 第 8.3 节；自动触发按 ADR-001 推迟）
    # 支持每用户额度覆盖（阶段 8 CLI 管理命令写入）
    limit = effective_limit(ctx.user.quota_overrides_json, "analysis_manual_daily")
    day_start = datetime.combine(datetime.now(UTC).date(), dtime.min, tzinfo=UTC)
    used = (
        await db.execute(
            select(func.count())
            .select_from(AgentRun)
            .where(
                AgentRun.user_id == ctx.user.id,
                AgentRun.trigger == "manual",
                AgentRun.created_at >= day_start,
            )
        )
    ).scalar_one()
    if used >= limit:
        raise AppError(
            code="RATE_LIMITED",
            message="今日手动深度分析次数已用完，明天再试",
            status_code=429,
            details={"limit": limit, "used": int(used)},
        )

    adapter = get_llm_adapter()
    run = AgentRun(
        recommendation_id=rec.id,
        user_id=ctx.user.id,
        trigger="manual",
        graph_version=GRAPH_VERSION,
        provider=adapter.provider,
        model_map_json={"standard_analysis": adapter.model_id},
        status="queued",
        current_stage="queued",
        input_fingerprint="pending",  # 任务执行时以实际输入文档指纹覆盖
        output_schema_version=OUTPUT_SCHEMA_VERSION,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    dispatch_task(run_standard_analysis_task, str(run.id))
    # eager（测试）模式下任务已同步完成：返回落库后的最新状态
    await db.refresh(run)
    return _run_out(run)


@router.get("/agent-runs/{run_id}", response_model=AgentRunOut)
async def get_agent_run(
    run_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AgentRunOut:
    """任务状态与最终报告；无内部对话/思维链可查。"""
    run = (
        await db.execute(select(AgentRun).where(AgentRun.id == run_id))
    ).scalar_one_or_none()
    if run is None or run.user_id != ctx.user.id:
        raise _NOT_FOUND
    return _run_out(run)


@router.get("/recommendations/{rec_id}/analyses", response_model=AgentRunListOut)
async def list_analyses(
    rec_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AgentRunListOut:
    """该推荐的分析任务列表（新→旧）。"""
    rec = await _get_owned_recommendation(db, rec_id, ctx)
    runs = (
        (
            await db.execute(
                select(AgentRun)
                .where(AgentRun.recommendation_id == rec.id)
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return AgentRunListOut(items=[_run_out(run) for run in runs])
