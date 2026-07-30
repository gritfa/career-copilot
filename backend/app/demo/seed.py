"""一键 demo 种子编排（阶段 11 P1）——幂等，可重复执行。

硬性约束（docs/15 P1，负责人书面 review）：
- **不加任何认证后门**：演示账号通过预置一次性邀请码 + Mailpit 收 magic link
  邮件走完全正常的注册/登录 API；不跳过邀请码、不直造会话。
- 种子岗位 data_origin='synthetic_seed'（DB 层）+ 前端「合成示例」徽标（展示层）。
- 内容红线：全部合成，不含真实公司在招岗位正文与真实个人信息。

幂等策略：
- 邀请码：按 code_hash upsert；
- 演示用户：已存在则跳过注册；
- 简历/事实：已有 active 事实则跳过；同文件重复上传走 sha256 去重；
- 种子岗位：稳定 source_job_id + 内容 hash 预检，未变化完全跳过（不新增快照）；
- 求职方案：按（用户, 方案名）幂等；
- 推荐：真实 Celery 任务本身幂等（同日重复执行只补新岗位）。
"""

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import hash_invite_code, normalize_email
from app.db.models import (
    CanonicalJob,
    Invite,
    JobPosting,
    JobSnapshot,
    JobSource,
    ProfileFact,
    Recommendation,
    SearchPlan,
    User,
)
from app.db.session import get_sessionmaker
from app.demo.data import (
    DEMO_EMAIL,
    DEMO_INVITE_LOGIN,
    DEMO_INVITE_REGISTER,
    DEMO_PLAN_NAME,
    DEMO_RESUME_FILENAME,
    build_demo_resume_docx,
    build_seed_job_payloads,
)
from app.jobs.adapters.base import RawJobSnapshot, SourceJobRef
from app.jobs.constants import DATA_ORIGIN_SYNTHETIC_SEED, SOURCE_KEY_SYNTHETIC_SEED
from app.jobs.pipeline import ingest_raw
from app.jobs.registry import seed_sources_sync
from app.main import create_app

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")


class SeedError(RuntimeError):
    """seed 过程中的明确失败（不静默、不伪装成功）。"""


@dataclass
class SeedReport:
    """seed 执行结果（供脚本打印与测试断言）。"""

    demo_email: str
    login_invite_code: str
    user_created: bool
    active_facts: int
    seed_jobs_created: int
    seed_jobs_total: int
    plan_created: bool
    matching_status: str
    recommendations_total: int


def format_report(report: SeedReport) -> str:
    lines = [
        "================ CareerCopilot demo seed 完成 ================",
        f"演示账号邮箱     : {report.demo_email}",
        f"登录邀请码       : {report.login_invite_code}",
        f"本次新建演示用户 : {'是' if report.user_created else '否（已存在，跳过注册）'}",
        f"已确认事实条数   : {report.active_facts}",
        f"种子岗位         : 本次新建 {report.seed_jobs_created} / 共 {report.seed_jobs_total}"
        "（data_origin=synthetic_seed，全部为合成内容）",
        f"求职方案         : {'本次创建' if report.plan_created else '已存在'}（{DEMO_PLAN_NAME}）",
        f"推荐生成         : {report.matching_status}，当前共 {report.recommendations_total} 条",
        "",
        "登录方式（正常流程，无后门）：",
        "  1. 打开前端登录页，填演示邮箱 + 上面的登录邀请码，请求登录链接",
        "  2. 打开 Mailpit（http://localhost:8025）收邮件，点击其中的登录链接",
        "==============================================================",
    ]
    return "\n".join(lines)


# ---------------- Mailpit ----------------


async def _mailpit_find_token(mailpit_api: str, to_email: str, timeout_seconds: float) -> str:
    """轮询 Mailpit，取发给 to_email 的最新一封邮件里的 magic link token。"""
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    async with httpx.AsyncClient(timeout=5) as http:
        while True:
            resp = await http.get(f"{mailpit_api}/messages")
            resp.raise_for_status()
            for msg in resp.json().get("messages", []):  # Mailpit 按时间倒序
                recipients = [addr["Address"].lower() for addr in msg.get("To", [])]
                if to_email.lower() not in recipients:
                    continue
                detail = await http.get(f"{mailpit_api}/message/{msg['ID']}")
                detail.raise_for_status()
                match = _TOKEN_RE.search(detail.json().get("Text", ""))
                if match:
                    return match.group(1)
            if asyncio.get_event_loop().time() >= deadline:
                raise SeedError(
                    f"在 Mailpit（{mailpit_api}）里没等到发给 {to_email} 的登录邮件；"
                    "请确认 Mailpit 容器在运行且后端 SMTP 配置指向它"
                )
            await asyncio.sleep(0.5)


# ---------------- 邀请码 / 用户 ----------------


async def _ensure_invite(app: FastAPI, code: str, max_uses: int) -> None:
    """按 code_hash 幂等预置邀请码（数据预置，校验/消耗仍走正常认证流程）。"""
    factory = get_sessionmaker(app)
    async with factory() as db:
        existing = (
            await db.execute(select(Invite).where(Invite.code_hash == hash_invite_code(code)))
        ).scalar_one_or_none()
        if existing is None:
            db.add(Invite(code_hash=hash_invite_code(code), max_uses=max_uses, used_count=0))
            await db.commit()


async def _find_demo_user(app: FastAPI) -> User | None:
    factory = get_sessionmaker(app)
    async with factory() as db:
        return (
            await db.execute(
                select(User).where(User.email_normalized == normalize_email(DEMO_EMAIL))
            )
        ).scalar_one_or_none()


async def _login_via_magic_link(
    client: AsyncClient, invite_code: str, mailpit_api: str, timeout_seconds: float
) -> None:
    """走正常 API 流程登录：请求 magic link → Mailpit 取 token → verify。"""
    resp = await client.post(
        "/api/v1/auth/magic-links",
        json={"email": DEMO_EMAIL, "invite_code": invite_code, "age_attested": True},
    )
    if resp.status_code != 202:
        raise SeedError(f"请求 magic link 失败：HTTP {resp.status_code} {resp.text}")
    token = await _mailpit_find_token(mailpit_api, DEMO_EMAIL, timeout_seconds)
    resp = await client.post("/api/v1/auth/magic-links/verify", json={"token": token})
    if resp.status_code != 200:
        raise SeedError(f"magic link 验证失败：HTTP {resp.status_code} {resp.text}")


# ---------------- 简历 / 事实库 ----------------


async def _count_active_facts(app: FastAPI, user_id: uuid.UUID) -> int:
    factory = get_sessionmaker(app)
    async with factory() as db:
        return int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ProfileFact)
                    .where(ProfileFact.user_id == user_id, ProfileFact.status == "active")
                )
            ).scalar_one()
        )


async def _upload_resume_and_confirm_facts(client: AsyncClient) -> None:
    """走正常 API：上传合成简历 → 解析（eager Celery）→ 全量接受候选事实。"""
    data = build_demo_resume_docx()
    resp = await client.post(
        "/api/v1/resumes/uploads",
        files={"file": (DEMO_RESUME_FILENAME, data, DOCX_MIME)},
    )
    if resp.status_code != 201:
        raise SeedError(f"简历上传失败：HTTP {resp.status_code} {resp.text}")
    upload_id = resp.json()["upload_id"]

    resp = await client.post("/api/v1/resumes", json={"upload_id": upload_id})
    if resp.status_code != 202:
        raise SeedError(f"简历确认失败：HTTP {resp.status_code} {resp.text}")
    resume_id = resp.json()["resume"]["id"]

    # eager 模式下解析已同步完成；读取解析结果与候选
    resp = await client.get(f"/api/v1/resumes/{resume_id}/parse")
    if resp.status_code != 200:
        raise SeedError(f"读取解析结果失败：HTTP {resp.status_code} {resp.text}")
    parse = resp.json()
    if parse["status"] != "succeeded":
        raise SeedError(f"简历解析未成功：status={parse['status']} error={parse['error_code']}")

    pending = [c for c in parse["candidates"] if c["status"] == "pending"]
    if not pending:
        return  # 没有待确认候选（此前已确认过）
    resp = await client.post(
        f"/api/v1/resumes/{resume_id}/facts/confirm",
        json={"decisions": [{"candidate_id": c["id"], "action": "accept"} for c in pending]},
    )
    if resp.status_code != 200:
        raise SeedError(f"确认事实失败：HTTP {resp.status_code} {resp.text}")


# ---------------- 种子岗位（同步管道，与连接器/导入同一条路径） ----------------


def _ensure_seed_source(db: Session) -> JobSource:
    seed_sources_sync(db)
    source = db.execute(
        select(JobSource).where(JobSource.source_key == SOURCE_KEY_SYNTHETIC_SEED)
    ).scalar_one_or_none()
    if source is None:
        source = JobSource(
            source_key=SOURCE_KEY_SYNTHETIC_SEED,
            name="合成示例数据（演示种子）",
            source_type="company_site",
            base_url=None,
            status="enabled",
            rate_limit_config={},
            capabilities_json={
                "mode": "synthetic_seed",
                "policy_status": "not_applicable",
                "notes": "阶段 11 P1 演示种子：全部内容合成，无任何真实来源抓取。",
            },
        )
        db.add(source)
        db.flush()
    return source


def seed_jobs_sync() -> tuple[int, int]:
    """幂等写入种子岗位；返回（本次新建数, 种子总数）。

    与用户导入/连接器同一条 snapshot → normalize → dedupe → canonical 管道；
    内容未变化时完全跳过（不新增快照行）。
    """
    payloads = build_seed_job_payloads()
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    created = 0
    try:
        with Session(engine) as db:
            source = _ensure_seed_source(db)
            for source_job_id, payload in payloads:
                content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                content_hash = hashlib.sha256(content).hexdigest()
                existing = db.execute(
                    select(JobPosting).where(
                        JobPosting.job_source_id == source.id,
                        JobPosting.source_job_id == source_job_id,
                    )
                ).scalar_one_or_none()
                if existing is not None and existing.snapshot_id is not None:
                    snapshot = db.get(JobSnapshot, existing.snapshot_id)
                    if snapshot is not None and snapshot.content_hash == content_hash:
                        continue  # 内容未变化：完全跳过
                raw = RawJobSnapshot(
                    ref=SourceJobRef(
                        source_key=SOURCE_KEY_SYNTHETIC_SEED,
                        source_job_id=source_job_id,
                        url="",
                    ),
                    content=content,
                    media_type="application/json",
                    fetched_at=datetime.now(UTC),
                    http_meta={"origin": "synthetic_seed"},
                )
                result = ingest_raw(db, source, raw)
                if result.created:
                    created += 1
            db.commit()

            # 自检：种子 canonical 必须全部带 synthetic_seed 标注（双重标注之 DB 层）
            mislabeled = db.execute(
                select(func.count())
                .select_from(CanonicalJob)
                .join(JobPosting, JobPosting.canonical_job_id == CanonicalJob.id)
                .where(
                    JobPosting.job_source_id == source.id,
                    CanonicalJob.data_origin != DATA_ORIGIN_SYNTHETIC_SEED,
                )
            ).scalar_one()
            if int(mislabeled) != 0:
                raise SeedError(
                    f"{mislabeled} 个种子岗位的 data_origin 不是 synthetic_seed（标注失效）"
                )
    finally:
        engine.dispose()
    return created, len(payloads)


# ---------------- 求职方案 / 推荐 ----------------


async def _find_demo_plan(app: FastAPI, user_id: uuid.UUID) -> SearchPlan | None:
    factory = get_sessionmaker(app)
    async with factory() as db:
        return (
            await db.execute(
                select(SearchPlan).where(
                    SearchPlan.user_id == user_id, SearchPlan.name == DEMO_PLAN_NAME
                )
            )
        ).scalar_one_or_none()


async def _create_demo_plan(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/search-plans",
        json={
            "name": DEMO_PLAN_NAME,
            "role_family": "backend_python",
            "city_codes": ["110100", "310100", "440300"],  # 北京/上海/深圳
            "work_modes": ["onsite", "hybrid"],
            "minimum_monthly_salary": 15000,
            "target_monthly_salary": 30000,
            "minimum_match_score": 65,
            "allow_outsourcing": False,
        },
    )
    if resp.status_code != 201:
        raise SeedError(f"创建求职方案失败：HTTP {resp.status_code} {resp.text}")


def _run_matching_sync(plan_id: uuid.UUID) -> str:
    """实跑一轮推荐生成（真实 Celery 任务代码，eager 同步执行）。"""
    from app.matching.tasks import generate_recommendations_task

    result = generate_recommendations_task.apply(args=(str(plan_id),)).get()
    return str(result.get("status", result))


async def _count_recommendations(app: FastAPI, plan_id: uuid.UUID) -> int:
    factory = get_sessionmaker(app)
    async with factory() as db:
        return int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Recommendation)
                    .where(Recommendation.search_plan_id == plan_id)
                )
            ).scalar_one()
        )


# ---------------- 编排入口 ----------------


async def run_demo_seed(
    mailpit_api: str = "http://localhost:8025/api/v1",
    mail_timeout_seconds: float = 20.0,
) -> SeedReport:
    """幂等执行全部 seed 步骤；任何一步明确失败即抛 SeedError。"""
    if not get_settings().celery_task_always_eager:
        raise SeedError(
            "seed 需要 CELERY_TASK_ALWAYS_EAGER=1（脚本入口已设置；"
            "若直接调用本函数请先设置环境变量），否则解析/推荐任务无法同步完成"
        )

    app = create_app()
    transport = ASGITransport(app=app)
    # https base_url：会话 cookie 是 Secure 的，走正常 Cookie 机制
    base_url = "https://demo.careercopilot.local"
    async with AsyncClient(transport=transport, base_url=base_url) as client:
        try:
            # 1) 预置邀请码（注册用 + 登录用），只做数据 upsert
            await _ensure_invite(app, DEMO_INVITE_REGISTER, max_uses=1)
            await _ensure_invite(app, DEMO_INVITE_LOGIN, max_uses=1)

            # 2) 演示用户：不存在则走完全正常的注册流程（邀请码 + Mailpit magic link）
            user = await _find_demo_user(app)
            user_created = False
            logged_in = False
            if user is None:
                await _login_via_magic_link(
                    client, DEMO_INVITE_REGISTER, mailpit_api, mail_timeout_seconds
                )
                logged_in = True
                user = await _find_demo_user(app)
                if user is None:
                    raise SeedError("注册流程完成但查不到演示用户（不应发生）")
                user_created = True

            async def ensure_session() -> None:
                nonlocal logged_in
                if not logged_in:
                    await _login_via_magic_link(
                        client, DEMO_INVITE_LOGIN, mailpit_api, mail_timeout_seconds
                    )
                    logged_in = True

            # 3) 合成简历 + 事实库（无 active 事实才执行；全走正常 API）
            if await _count_active_facts(app, user.id) == 0:
                await ensure_session()
                await _upload_resume_and_confirm_facts(client)
            active_facts = await _count_active_facts(app, user.id)
            if active_facts == 0:
                raise SeedError("事实确认后事实库仍为空（解析或确认失败）")

            # 4) 种子岗位（幂等，与导入同一管道）
            seed_created, seed_total = await asyncio.to_thread(seed_jobs_sync)

            # 5) 求职方案（按名称幂等；创建走正常 API）
            plan = await _find_demo_plan(app, user.id)
            plan_created = False
            if plan is None:
                await ensure_session()
                await _create_demo_plan(client)
                plan = await _find_demo_plan(app, user.id)
                if plan is None:
                    raise SeedError("方案创建成功但查不到（不应发生）")
                plan_created = True

            # 6) 实跑一轮推荐
            matching_status = await asyncio.to_thread(_run_matching_sync, plan.id)
            rec_total = await _count_recommendations(app, plan.id)
            if rec_total == 0:
                raise SeedError(
                    f"推荐生成后数量为 0（matching status={matching_status}）；"
                    "demo 用户登录后将无内容可看，视为 seed 失败"
                )

            return SeedReport(
                demo_email=DEMO_EMAIL,
                login_invite_code=DEMO_INVITE_LOGIN,
                user_created=user_created,
                active_facts=active_facts,
                seed_jobs_created=seed_created,
                seed_jobs_total=seed_total,
                plan_created=plan_created,
                matching_status=matching_status,
                recommendations_total=rec_total,
            )
        finally:
            from app.core.redis import close_redis
            from app.db.session import dispose_engine

            await dispose_engine(app)
            await close_redis(app)
