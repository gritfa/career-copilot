"""求职方案与公司偏好 API（docs/04 第 5 节）。

- 每用户最多 3 个 active：应用层 advisory xact lock + 计数，DB 触发器兜底，
  并发超限稳定返回 409 SEARCH_PLAN_ACTIVE_LIMIT。
- 越权访问他人方案一律 404（防 IDOR）。
- 审计只记 ID/动作/计数，不含名称正文。
"""

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.auth.deps import AuthContext, ensure_owner, require_active_user, require_user
from app.companies.resolver import company_name_fields
from app.core.errors import AppError
from app.core.security import hash_ip
from app.db.models import Company, CompanyPreference, LearnedPreference, SearchPlan
from app.db.session import get_db
from app.jobs.adapters.boss import build_plan_search_urls
from app.search_plans.schemas import (
    CompanyPreferenceOut,
    CompanyPreferencesOut,
    CompanyPreferencesPutRequest,
    LinkOutURL,
    ResetLearnedOut,
    SearchPlanCreateRequest,
    SearchPlanListOut,
    SearchPlanOut,
    SearchPlanUpdateRequest,
)

router = APIRouter(tags=["search-plans"])

MAX_ACTIVE_PLANS = 3
_ACTIVE_LIMIT_ERROR = AppError(
    code="SEARCH_PLAN_ACTIVE_LIMIT",
    message=f"最多同时保留 {MAX_ACTIVE_PLANS} 个 active 求职方案",
    status_code=409,
)


def _plan_out(plan: SearchPlan) -> SearchPlanOut:
    return SearchPlanOut(
        id=plan.id,
        name=plan.name,
        role_family=plan.role_family,
        status=plan.status,
        priority=plan.priority,
        city_codes=list(plan.city_codes),
        work_modes=list(plan.work_modes),
        salary_currency=plan.salary_currency,
        minimum_monthly_salary=plan.minimum_monthly_salary,
        target_monthly_salary=plan.target_monthly_salary,
        salary_months_preference=plan.salary_months_preference,
        minimum_match_score=plan.minimum_match_score,
        allow_outsourcing=plan.allow_outsourcing,
        base_resume_version_id=plan.base_resume_version_id,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        # boss 适配器返回 dict（不反向依赖本模块 schema），此处运行时真校验成模型
        link_out_urls=[
            LinkOutURL.model_validate(item)
            for item in build_plan_search_urls(plan.role_family, list(plan.city_codes))
        ],
    )


async def _lock_and_check_active_limit(
    db: AsyncSession, user_id: uuid.UUID, *, exclude_plan_id: uuid.UUID | None = None
) -> None:
    """取用户级 advisory xact lock 后计数（与 DB 触发器同一把锁，事务结束释放）。"""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('search_plans_active:' || :uid))"),
        {"uid": str(user_id)},
    )
    stmt = select(func.count()).where(
        SearchPlan.user_id == user_id, SearchPlan.status == "active"
    )
    if exclude_plan_id is not None:
        stmt = stmt.where(SearchPlan.id != exclude_plan_id)
    count = (await db.execute(stmt)).scalar_one()
    if count >= MAX_ACTIVE_PLANS:
        raise _ACTIVE_LIMIT_ERROR


def _is_active_limit_dbapi_error(exc: DBAPIError) -> bool:
    return "active search plan limit" in str(exc.orig)


async def _get_owned_plan(
    db: AsyncSession, plan_id: uuid.UUID, ctx: AuthContext
) -> SearchPlan:
    plan = (
        await db.execute(select(SearchPlan).where(SearchPlan.id == plan_id))
    ).scalar_one_or_none()
    ensure_owner(plan.user_id if plan else None, ctx.user)
    assert plan is not None
    return plan


# ---------------- CRUD ----------------


@router.get("/search-plans", response_model=SearchPlanListOut)
async def list_search_plans(
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> SearchPlanListOut:
    plans = (
        (
            await db.execute(
                select(SearchPlan)
                .where(SearchPlan.user_id == ctx.user.id)
                .order_by(SearchPlan.priority.desc(), SearchPlan.created_at)
            )
        )
        .scalars()
        .all()
    )
    return SearchPlanListOut(items=[_plan_out(p) for p in plans])


@router.post(
    "/search-plans", response_model=SearchPlanOut, status_code=status.HTTP_201_CREATED
)
async def create_search_plan(
    payload: SearchPlanCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> SearchPlanOut:
    if payload.status == "active":
        await _lock_and_check_active_limit(db, ctx.user.id)

    plan = SearchPlan(user_id=ctx.user.id, **payload.model_dump())
    db.add(plan)
    try:
        await db.flush()
    except DBAPIError as exc:  # DB 触发器兜底（并发窗口）
        await db.rollback()
        if _is_active_limit_dbapi_error(exc):
            raise _ACTIVE_LIMIT_ERROR from exc
        raise
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="search_plan_created",
        resource_type="search_plan",
        resource_id=str(plan.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return _plan_out(plan)


@router.get("/search-plans/{plan_id}", response_model=SearchPlanOut)
async def get_search_plan(
    plan_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> SearchPlanOut:
    return _plan_out(await _get_owned_plan(db, plan_id, ctx))


@router.patch("/search-plans/{plan_id}", response_model=SearchPlanOut)
async def update_search_plan(
    plan_id: uuid.UUID,
    payload: SearchPlanUpdateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> SearchPlanOut:
    plan = await _get_owned_plan(db, plan_id, ctx)
    updates = payload.model_dump(exclude_unset=True)

    # 薪资一致性：结合现有值校验 minimum ≤ target（DB CHECK 兜底）
    new_min = updates.get("minimum_monthly_salary", plan.minimum_monthly_salary)
    new_target = updates.get("target_monthly_salary", plan.target_monthly_salary)
    if new_min is not None and new_target is not None and new_min > new_target:
        raise AppError(
            code="VALIDATION_ERROR",
            message="最低月薪不能高于目标月薪",
            status_code=422,
        )

    if updates.get("status") == "active" and plan.status != "active":
        await _lock_and_check_active_limit(db, ctx.user.id, exclude_plan_id=plan.id)

    for field_name, value in updates.items():
        setattr(plan, field_name, value)
    try:
        await db.flush()
    except DBAPIError as exc:
        await db.rollback()
        if _is_active_limit_dbapi_error(exc):
            raise _ACTIVE_LIMIT_ERROR from exc
        raise
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="search_plan_updated",
        resource_type="search_plan",
        resource_id=str(plan.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return _plan_out(plan)


@router.delete("/search-plans/{plan_id}")
async def delete_search_plan(
    plan_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """删除方案（不自动删除简历版本，docs/04）。"""
    plan = await _get_owned_plan(db, plan_id, ctx)
    await db.delete(plan)
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="search_plan_deleted",
        resource_type="search_plan",
        resource_id=str(plan_id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return {"status": "deleted"}


@router.post("/search-plans/{plan_id}/activate", response_model=SearchPlanOut)
async def activate_search_plan(
    plan_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> SearchPlanOut:
    plan = await _get_owned_plan(db, plan_id, ctx)
    if plan.status != "active":
        await _lock_and_check_active_limit(db, ctx.user.id, exclude_plan_id=plan.id)
        plan.status = "active"
        try:
            await db.flush()
        except DBAPIError as exc:
            await db.rollback()
            if _is_active_limit_dbapi_error(exc):
                raise _ACTIVE_LIMIT_ERROR from exc
            raise
    # 基础推荐刷新属阶段 5（匹配）；此处只记录激活事件
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="search_plan_activated",
        resource_type="search_plan",
        resource_id=str(plan.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return _plan_out(plan)


# ---------------- 公司偏好 ----------------


@router.get(
    "/search-plans/{plan_id}/company-preferences", response_model=CompanyPreferencesOut
)
async def get_company_preferences(
    plan_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> CompanyPreferencesOut:
    plan = await _get_owned_plan(db, plan_id, ctx)
    rows = (
        await db.execute(
            select(CompanyPreference, Company)
            .join(Company, Company.id == CompanyPreference.company_id)
            .where(CompanyPreference.search_plan_id == plan.id)
            .order_by(Company.canonical_name)
        )
    ).all()
    return CompanyPreferencesOut(
        items=[
            CompanyPreferenceOut(
                company_id=company.id,
                company_name=company.canonical_name,
                preference=pref.preference,
            )
            for pref, company in rows
        ]
    )


@router.put(
    "/search-plans/{plan_id}/company-preferences", response_model=CompanyPreferencesOut
)
async def put_company_preferences(
    plan_id: uuid.UUID,
    payload: CompanyPreferencesPutRequest,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> CompanyPreferencesOut:
    """整体替换方案的公司偏好（同名公司重复出现时后者生效）。"""
    plan = await _get_owned_plan(db, plan_id, ctx)

    # 同名去重：后者生效
    wanted: dict[str, str] = {
        item.company_name.strip(): item.preference for item in payload.items
    }

    await db.execute(
        delete(CompanyPreference).where(CompanyPreference.search_plan_id == plan.id)
    )
    out_items: list[CompanyPreferenceOut] = []
    for company_name, preference in wanted.items():
        company = (
            await db.execute(
                select(Company).where(Company.canonical_name == company_name)
            )
        ).scalar_one_or_none()
        if company is None:
            company = Company(
                canonical_name=company_name,
                aliases=[],
                source_refs_json=[],
                **company_name_fields(company_name),
            )
            db.add(company)
            await db.flush()
        db.add(
            CompanyPreference(
                search_plan_id=plan.id, company_id=company.id, preference=preference
            )
        )
        out_items.append(
            CompanyPreferenceOut(
                company_id=company.id,
                company_name=company.canonical_name,
                preference=preference,
            )
        )
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="company_preferences_updated",
        resource_type="search_plan",
        resource_id=str(plan.id),
        reason_code=f"count:{len(out_items)}",
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    out_items.sort(key=lambda item: item.company_name)
    return CompanyPreferencesOut(items=out_items)


# ---------------- 学习偏好重置 ----------------


@router.post("/preferences/reset-learned", response_model=ResetLearnedOut)
async def reset_learned_preferences(
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> ResetLearnedOut:
    """一键重置反馈学习偏好：历史版本标记 reset（保留可追溯），新建空版本。"""
    rows = (
        (
            await db.execute(
                select(LearnedPreference).where(
                    LearnedPreference.user_id == ctx.user.id,
                    LearnedPreference.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    max_version = (
        await db.execute(
            select(func.coalesce(func.max(LearnedPreference.version), 0)).where(
                LearnedPreference.user_id == ctx.user.id
            )
        )
    ).scalar_one()
    for row in rows:
        row.status = "reset"
    new_version = int(max_version) + 1
    db.add(
        LearnedPreference(
            user_id=ctx.user.id,
            version=new_version,
            weights_json={},
            status="active",
            derived_from="reset",
        )
    )
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="learned_preferences_reset",
        resource_type="learned_preference",
        resource_id=str(ctx.user.id),
        reason_code=f"new_version:{new_version}",
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return ResetLearnedOut(status="reset", new_version=new_version)
