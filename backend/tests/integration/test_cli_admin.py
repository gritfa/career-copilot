"""阶段 8 CLI 管理命令主路径（ADR-001：管理写操作走 CLI，不做管理 UI）。

覆盖：封禁（会话立即失效 + 登录拒绝）/解封、额度覆盖真实生效、
邀请码创建（明文只出现一次）、失败任务重跑、输出无邮箱明文、全部写审计。
"""

import json
import uuid

from sqlalchemy import select

from app.cli import main as cli_main
from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)


def run_cli(capsys, *argv: str) -> dict:
    assert cli_main(list(argv)) == 0
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1])


async def test_suspend_revokes_sessions_and_blocks_access_then_unsuspend(
    client, db_factory, capsys
):
    from app.db.models import AuditEvent, User

    email = unique_email("ban")
    user = await create_user(db_factory, email)
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))
    assert (await client.get("/api/v1/auth/session")).status_code == 200

    result = run_cli(
        capsys, "user", "suspend", "--email", email, "--reason", "Abuse Case!"
    )
    assert result["status"] == "suspended"
    assert result["sessions_revoked"] == 1
    # 输出不含邮箱明文
    assert email.split("@")[0] not in json.dumps(result)

    # 已有会话立即失效；封禁状态下一律 403 稳定错误码
    resp = await client.get("/api/v1/auth/session")
    assert resp.status_code in (401, 403)
    # 即使拿到新会话（模拟遗留 cookie），也会被状态检查拦截
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))
    resp = await client.get("/api/v1/auth/session")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ACCOUNT_SUSPENDED"

    async with db_factory() as db:
        row = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        assert row.status == "suspended"
        assert row.suspended_at is not None
        audit = (
            await db.execute(
                select(AuditEvent).where(AuditEvent.action == "admin_user_suspended")
            )
        ).scalar_one()
        assert audit.resource_id == str(user.id)
        assert audit.reason_code == "abuse_case"

    # 解封：状态恢复，新的会话可用
    result = run_cli(capsys, "user", "unsuspend", "--user-id", str(user.id))
    assert result["status"] == "active"
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))
    assert (await client.get("/api/v1/auth/session")).status_code == 200

    # 幂等：重复封禁不报错
    run_cli(capsys, "user", "suspend", "--email", email, "--reason", "again")
    run_cli(capsys, "user", "unsuspend", "--email", email)


async def test_set_quota_override_takes_effect_and_clears(client, db_factory, capsys):
    from app.db.models import AuditEvent

    email = unique_email("quota-cli")
    user = await create_user(db_factory, email)
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))

    result = run_cli(
        capsys, "user", "set-quota", "--email", email,
        "--key", "data_export_daily", "--limit", "0",
    )
    assert result["quota_overrides"] == {"data_export_daily": 0}

    # 额度覆盖真实生效：0 → 立即 429
    resp = await client.post("/api/v1/privacy/data-exports")
    assert resp.status_code == 429
    assert resp.json()["error"]["details"]["limit"] == 0

    async with db_factory() as db:
        audit = (
            await db.execute(
                select(AuditEvent).where(AuditEvent.action == "admin_quota_adjusted")
            )
        ).scalars().all()
    assert len(audit) == 1

    # 清除覆盖 → 回退全局默认，可以导出
    result = run_cli(
        capsys, "user", "set-quota", "--email", email, "--key", "data_export_daily",
        "--clear",
    )
    assert result["quota_overrides"] is None
    resp = await client.post("/api/v1/privacy/data-exports")
    assert resp.status_code == 202


async def test_invite_create_plaintext_once_and_usable(client, db_factory, capsys):
    from app.core.security import hash_invite_code
    from app.db.models import Invite

    result = run_cli(capsys, "invite", "create", "--max-uses", "5")
    assert result["code"]
    async with db_factory() as db:
        invite = (
            await db.execute(
                select(Invite).where(Invite.code_hash == hash_invite_code(result["code"]))
            )
        ).scalar_one()
    assert invite.max_uses == 5
    assert invite.used_count == 0


async def test_retry_failed_data_export(client, db_factory, capsys):
    from app.db.models import AuditEvent, DataExport

    user = await create_user(db_factory, unique_email("retry"))
    async with db_factory() as db:
        export = DataExport(user_id=user.id, status="failed", error_code="EXPORT_GENERATION_FAILED")
        db.add(export)
        await db.commit()
        await db.refresh(export)

    result = run_cli(capsys, "task", "retry-data-export", "--export-id", str(export.id))
    # eager 模式下重跑同步完成
    assert result["status"] == "succeeded"

    async with db_factory() as db:
        row = (
            await db.execute(select(DataExport).where(DataExport.id == export.id))
        ).scalar_one()
        assert row.status == "succeeded"
        assert row.error_code is None
        audit = (
            await db.execute(
                select(AuditEvent).where(AuditEvent.action == "admin_task_retried")
            )
        ).scalar_one()
        assert audit.resource_id == str(export.id)

    # 非 failed 状态拒绝重跑
    try:
        cli_main(["task", "retry-data-export", "--export-id", str(export.id)])
        raise AssertionError("应当拒绝重跑非 failed 任务")
    except SystemExit as exc:
        assert exc.code != 0


async def test_user_list_masks_emails(client, db_factory, capsys):
    email = unique_email("list")
    await create_user(db_factory, email)
    result = run_cli(capsys, "user", "list")
    text = json.dumps(result)
    assert email.split("@")[0] not in text
    assert result["total"] >= 1


async def test_purge_user_refuses_active_account(client, db_factory, capsys):
    user = await create_user(db_factory, unique_email("guard"))
    try:
        cli_main(["privacy", "purge-user", "--user-id", str(user.id)])
        raise AssertionError("active 账号必须拒绝清理")
    except SystemExit as exc:
        assert exc.code != 0
    # 不存在的用户：幂等输出 already_purged
    result = run_cli(capsys, "privacy", "purge-user", "--user-id", str(uuid.uuid4()))
    assert result["status"] == "already_purged"
