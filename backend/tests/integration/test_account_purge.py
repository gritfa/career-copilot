"""阶段 8 注销硬删闭环：软删 → 宽限期 → 物理清理（文件/向量/DB 真删）。

覆盖：beat 任务全链路、可验证清单、部分失败不标成功且可安全重试、
active 账号绝不误删、清理后旧会话失效（docs/08 第 9 节）。
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)
from tests.integration.resume_files import make_pdf
from tests.integration.test_standard_analysis import setup_recommendations


async def _make_purge_due(db_factory, user_id) -> None:
    from app.db.models import User

    async with db_factory() as db:
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(purge_after=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()


def _sync_session() -> tuple[SyncSession, object]:
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    return SyncSession(engine), engine


async def test_purge_task_hard_deletes_rows_vectors_and_files(client, db_factory):
    from app.db.models import (
        AccountPurgeRun,
        AuditEvent,
        ProfileFact,
        ProfileVector,
        Recommendation,
        Resume,
        SearchPlan,
        UsageLedger,
        User,
    )
    from app.integrations.storage import get_storage

    # 完整数据图：事实/方案/推荐/向量 + 上传简历（原件 + 解析文本文件）+ 定制版本 + 导出
    user, _plan, items = await setup_recommendations(
        db_factory, client, "purge-main@cc-integration.dev"
    )
    up = await client.post(
        "/api/v1/resumes/uploads",
        files={"file": ("resume.pdf", make_pdf(), "application/pdf")},
    )
    assert up.status_code == 201
    assert (
        await client.post("/api/v1/resumes", json={"upload_id": up.json()["upload_id"]})
    ).status_code == 202
    draft = (
        await client.post(f"/api/v1/recommendations/{items[0]['id']}/resume-drafts")
    ).json()
    assert (
        await client.post(f"/api/v1/resume-versions/{draft['id']}/confirm")
    ).status_code == 200
    export = (
        await client.post(
            f"/api/v1/resume-versions/{draft['id']}/exports", json={"format": "pdf"}
        )
    ).json()
    assert export["status"] == "succeeded"

    # 存储对象在删除前确实存在
    storage = get_storage()
    async with db_factory() as db:
        resume_keys = list(
            (
                await db.execute(select(Resume.storage_key).where(Resume.user_id == user.id))
            ).scalars()
        )
        vectors_before = len(
            (
                await db.execute(
                    select(ProfileVector.id)
                    .join(SearchPlan, SearchPlan.id == ProfileVector.search_plan_id)
                    .where(SearchPlan.user_id == user.id)
                )
            ).all()
        )
    assert resume_keys and all(storage.exists(k) for k in resume_keys)
    assert vectors_before >= 1

    # 软删 → 宽限期到期 → beat 任务清理
    assert (await client.post("/api/v1/privacy/account-deletion")).status_code == 202
    await _make_purge_due(db_factory, user.id)

    from app.privacy.tasks import purge_due_accounts_task

    result = purge_due_accounts_task.apply().get()
    assert result == {"due": 1, "succeeded": 1, "failed": 0}

    async with db_factory() as db:
        # 用户行与全部归属行真删
        assert (
            await db.execute(select(User).where(User.id == user.id))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(select(ProfileFact).where(ProfileFact.user_id == user.id))
        ).first() is None
        assert (
            await db.execute(select(SearchPlan).where(SearchPlan.user_id == user.id))
        ).first() is None
        # 向量真删（pgvector 行随方案级联）
        assert (await db.execute(select(ProfileVector))).first() is None
        assert (await db.execute(select(Recommendation))).first() is None
        # 费用账本保留但已匿名化（user_id 置空）
        led = (await db.execute(select(UsageLedger))).scalars().all()
        assert all(row.user_id is None for row in led)

        # 可验证清单
        run = (
            await db.execute(
                select(AccountPurgeRun).where(AccountPurgeRun.user_id == user.id)
            )
        ).scalar_one()
        assert run.status == "succeeded"
        assert run.completed_at is not None
        m = run.manifest_json
        assert m["resumes"] == 1
        assert m["search_plans"] == 1
        assert m["profile_vectors"] == vectors_before
        assert m["files_failed"] == 0
        assert m["files_deleted"] >= 2  # 至少：简历原件 + 解析文本/导出文件

        # 审计事件（只有 ID，无邮箱/正文）
        audit = (
            await db.execute(select(AuditEvent).where(AuditEvent.action == "account_purged"))
        ).scalar_one()
        assert audit.actor_type == "system"
        assert audit.resource_id == str(user.id)

    # 文件真删
    assert all(not storage.exists(k) for k in resume_keys)

    # 清理后旧会话失效：数据访问一律 401
    resp = await client.get("/api/v1/auth/session")
    assert resp.status_code == 401


async def test_partial_file_failure_marks_failed_and_is_retryable(client, db_factory):
    from app.db.models import AccountPurgeRun, User
    from app.integrations.storage import LocalStorageAdapter

    user, _plan, _items = await setup_recommendations(
        db_factory, client, "purge-retry@cc-integration.dev"
    )
    up = await client.post(
        "/api/v1/resumes/uploads",
        files={"file": ("resume.pdf", make_pdf(), "application/pdf")},
    )
    assert (
        await client.post("/api/v1/resumes", json={"upload_id": up.json()["upload_id"]})
    ).status_code == 202
    assert (await client.post("/api/v1/privacy/account-deletion")).status_code == 202
    await _make_purge_due(db_factory, user.id)

    from app.privacy.service import execute_account_purge

    # 注入文件删除故障：子项失败 → 整体 failed，DB 行必须原样保留
    original_delete = LocalStorageAdapter.delete

    def broken_delete(self, key):  # noqa: ANN001
        raise OSError("disk failure (injected)")

    LocalStorageAdapter.delete = broken_delete
    try:
        db, engine = _sync_session()
        try:
            outcome = execute_account_purge(db, user.id)
        finally:
            db.close()
            engine.dispose()
    finally:
        LocalStorageAdapter.delete = original_delete

    assert outcome["status"] == "failed"
    async with db_factory() as db:
        assert (
            await db.execute(select(User).where(User.id == user.id))
        ).scalar_one_or_none() is not None, "文件删除失败时不允许删 DB 行"
        run = (
            await db.execute(
                select(AccountPurgeRun).where(AccountPurgeRun.user_id == user.id)
            )
        ).scalar_one()
        assert run.status == "failed"
        assert run.error_code == "FILE_DELETE_FAILED"
        assert run.attempts == 1

    # 故障恢复后安全重试：同一 run 置成功，用户真删
    db, engine = _sync_session()
    try:
        outcome = execute_account_purge(db, user.id)
    finally:
        db.close()
        engine.dispose()
    assert outcome["status"] == "succeeded"

    async with db_factory() as db:
        assert (
            await db.execute(select(User).where(User.id == user.id))
        ).scalar_one_or_none() is None
        run = (
            await db.execute(
                select(AccountPurgeRun).where(AccountPurgeRun.user_id == user.id)
            )
        ).scalar_one()
        assert run.status == "succeeded"
        assert run.attempts == 2


async def test_active_account_is_never_purged(client, db_factory):
    from app.db.models import User

    user = await create_user(db_factory, unique_email("safe"))
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))

    from app.privacy.service import execute_account_purge

    db, engine = _sync_session()
    try:
        outcome = execute_account_purge(db, user.id)
    finally:
        db.close()
        engine.dispose()
    assert outcome["status"] == "not_pending"

    async with db_factory() as db:
        assert (
            await db.execute(select(User).where(User.id == user.id))
        ).scalar_one_or_none() is not None
    # 不存在的用户：幂等返回 already_purged
    db, engine = _sync_session()
    try:
        assert execute_account_purge(db, uuid.uuid4())["status"] == "already_purged"
    finally:
        db.close()
        engine.dispose()
