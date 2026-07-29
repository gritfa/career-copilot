"""管理 CLI（阶段 8，ADR-001 减配：管理写操作全部走 CLI，不做管理 UI）。

用法（backend/ 下）：
    uv run python -m app.cli user list
    uv run python -m app.cli user suspend --email a@b.com --reason abuse
    uv run python -m app.cli user unsuspend --email a@b.com
    uv run python -m app.cli user set-quota --email a@b.com --key resume_tailor_daily --limit 5
    uv run python -m app.cli invite create --max-uses 10
    uv run python -m app.cli task retry-export --export-id <uuid>
    uv run python -m app.cli task retry-data-export --export-id <uuid>
    uv run python -m app.cli privacy run-purges
    uv run python -m app.cli privacy purge-user --user-id <uuid>
    uv run python -m app.cli privacy cleanup-exports

红线：
- 所有变更写审计（actor_type=admin，reason_code=cli）。
- 输出/日志不含邮箱明文（脱敏显示）、token、正文。
- 封禁立即撤销全部会话；解封不恢复旧会话。
"""

import argparse
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.audit.service import record_audit_sync
from app.core.config import get_settings
from app.core.quotas import QUOTA_KEYS
from app.core.security import generate_invite_code, hash_invite_code, normalize_email
from app.db.models import DataExport, Invite, ResumeExport, User
from app.db.models import Session as DbSession


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[0] if local else '*'}***@{domain}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", text.lower()).strip("_")[:64] or "cli"


def _open_session() -> tuple[Session, Any]:
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    return Session(engine), engine


def _resolve_user(db: Session, user_id: str | None, email: str | None) -> User:
    if user_id:
        user = db.get(User, uuid.UUID(user_id))
    elif email:
        user = db.execute(
            select(User).where(User.email_normalized == normalize_email(email))
        ).scalar_one_or_none()
    else:
        raise SystemExit("必须提供 --user-id 或 --email")
    if user is None:
        raise SystemExit("用户不存在")
    return user


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


# ---------------- user ----------------


def cmd_user_list(db: Session, args: argparse.Namespace) -> None:
    stmt = select(User).order_by(User.created_at.desc())
    if args.status:
        stmt = stmt.where(User.status == args.status)
    users = db.execute(stmt).scalars().all()
    _emit(
        {
            "total": len(users),
            "items": [
                {
                    "id": str(u.id),
                    "email_masked": _mask_email(u.email_normalized),
                    "role": u.role,
                    "status": u.status,
                    "quota_overrides": u.quota_overrides_json,
                    "created_at": u.created_at,
                }
                for u in users
            ],
        }
    )


def cmd_user_suspend(db: Session, args: argparse.Namespace) -> None:
    user = _resolve_user(db, args.user_id, args.email)
    if user.status == "suspended":
        _emit({"status": "suspended", "changed": False})
        return
    if user.status == "deletion_pending":
        raise SystemExit("账号处于注销恢复期，不做封禁（如需立即清理请走 privacy purge-user）")
    user.status = "suspended"
    user.suspended_at = datetime.now(UTC)
    # 立即撤销全部会话
    revoked = db.execute(
        update(DbSession)
        .where(DbSession.user_id == user.id, DbSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    ).rowcount
    record_audit_sync(
        db,
        actor_type="admin",
        action="admin_user_suspended",
        resource_type="user",
        resource_id=str(user.id),
        reason_code=_slug(args.reason),
    )
    db.commit()
    _emit(
        {
            "status": "suspended",
            "changed": True,
            "user_id": str(user.id),
            "sessions_revoked": int(revoked),
        }
    )


def cmd_user_unsuspend(db: Session, args: argparse.Namespace) -> None:
    user = _resolve_user(db, args.user_id, args.email)
    if user.status != "suspended":
        raise SystemExit(f"用户当前状态为 {user.status}，无需解封")
    user.status = "active"
    user.suspended_at = None
    record_audit_sync(
        db,
        actor_type="admin",
        action="admin_user_unsuspended",
        resource_type="user",
        resource_id=str(user.id),
        reason_code="cli",
    )
    db.commit()
    _emit({"status": "active", "changed": True, "user_id": str(user.id)})


def cmd_user_set_quota(db: Session, args: argparse.Namespace) -> None:
    user = _resolve_user(db, args.user_id, args.email)
    if args.key not in QUOTA_KEYS:
        raise SystemExit(f"未知额度键：{args.key}；可用：{', '.join(sorted(QUOTA_KEYS))}")
    overrides = dict(user.quota_overrides_json or {})
    if args.clear:
        overrides.pop(args.key, None)
    else:
        if args.limit is None or args.limit < 0:
            raise SystemExit("必须提供 --limit N（N >= 0）或 --clear")
        overrides[args.key] = args.limit
    user.quota_overrides_json = overrides or None
    record_audit_sync(
        db,
        actor_type="admin",
        action="admin_quota_adjusted",
        resource_type="user",
        resource_id=str(user.id),
        reason_code=_slug(f"{args.key}_{'clear' if args.clear else args.limit}"),
    )
    db.commit()
    _emit({"user_id": str(user.id), "quota_overrides": user.quota_overrides_json})


# ---------------- invite ----------------


def cmd_invite_create(db: Session, args: argparse.Namespace) -> None:
    code = generate_invite_code()
    invite = Invite(code_hash=hash_invite_code(code), max_uses=args.max_uses, used_count=0)
    db.add(invite)
    db.flush()
    record_audit_sync(
        db,
        actor_type="admin",
        action="admin_invite_created",
        resource_type="invite",
        resource_id=str(invite.id),
        reason_code="cli",
    )
    db.commit()
    # 明文只在这里出现一次；库里只存哈希
    _emit({"invite_id": str(invite.id), "code": code, "max_uses": args.max_uses})


# ---------------- task ----------------


def _retry_export(db: Session, model, export_id: str, task, action: str) -> None:
    from app.tasks.celery_app import dispatch_task

    export = db.get(model, uuid.UUID(export_id))
    if export is None:
        raise SystemExit("导出任务不存在")
    if export.status != "failed":
        raise SystemExit(f"只有 failed 状态可重跑（当前 {export.status}）")
    export.status = "queued"
    export.error_code = None
    export.completed_at = None
    record_audit_sync(
        db,
        actor_type="admin",
        action=action,
        resource_type=model.__tablename__,
        resource_id=str(export.id),
        reason_code="cli",
    )
    db.commit()
    dispatch_task(task, str(export.id))
    db.refresh(export)
    _emit({"export_id": str(export.id), "status": export.status})


def cmd_task_retry_export(db: Session, args: argparse.Namespace) -> None:
    from app.tailoring.tasks import export_resume_task

    _retry_export(db, ResumeExport, args.export_id, export_resume_task, "admin_task_retried")


def cmd_task_retry_data_export(db: Session, args: argparse.Namespace) -> None:
    from app.privacy.tasks import generate_data_export_task

    _retry_export(
        db, DataExport, args.export_id, generate_data_export_task, "admin_task_retried"
    )


# ---------------- privacy ----------------


def cmd_privacy_run_purges(db: Session, args: argparse.Namespace) -> None:
    from app.privacy.service import purge_due_accounts

    _emit(purge_due_accounts(db))


def cmd_privacy_purge_user(db: Session, args: argparse.Namespace) -> None:
    """立即清理一个 deletion_pending 账号（不等宽限期；active 账号一律拒绝）。"""
    from app.privacy.service import execute_account_purge

    result = execute_account_purge(db, uuid.UUID(args.user_id))
    if result["status"] == "not_pending":
        raise SystemExit("该账号未处于注销恢复期，拒绝清理（先由用户发起注销）")
    _emit(result)


def cmd_privacy_cleanup_exports(db: Session, args: argparse.Namespace) -> None:
    from app.privacy.service import cleanup_expired_export_files

    _emit(cleanup_expired_export_files(db))


# ---------------- 入口 ----------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="CareerCopilot 管理命令（阶段 8）"
    )
    sub = parser.add_subparsers(dest="group", required=True)

    user = sub.add_parser("user", help="用户管理").add_subparsers(dest="cmd", required=True)
    p = user.add_parser("list", help="用户列表（邮箱脱敏）")
    p.add_argument("--status", choices=["active", "suspended", "deletion_pending"])
    p.set_defaults(func=cmd_user_list)
    for name, func in (("suspend", cmd_user_suspend), ("unsuspend", cmd_user_unsuspend)):
        p = user.add_parser(name)
        p.add_argument("--user-id")
        p.add_argument("--email")
        if name == "suspend":
            p.add_argument("--reason", required=True, help="封禁原因（进入审计 reason_code）")
        p.set_defaults(func=func)
    p = user.add_parser("set-quota", help="每用户额度覆盖")
    p.add_argument("--user-id")
    p.add_argument("--email")
    p.add_argument("--key", required=True, help=f"额度键：{', '.join(sorted(QUOTA_KEYS))}")
    p.add_argument("--limit", type=int)
    p.add_argument("--clear", action="store_true", help="清除覆盖，回退全局默认")
    p.set_defaults(func=cmd_user_set_quota)

    invite = sub.add_parser("invite", help="邀请码").add_subparsers(dest="cmd", required=True)
    p = invite.add_parser("create")
    p.add_argument("--max-uses", type=int, default=1)
    p.set_defaults(func=cmd_invite_create)

    task = sub.add_parser("task", help="任务重跑").add_subparsers(dest="cmd", required=True)
    p = task.add_parser("retry-export", help="重跑失败的简历导出")
    p.add_argument("--export-id", required=True)
    p.set_defaults(func=cmd_task_retry_export)
    p = task.add_parser("retry-data-export", help="重跑失败的数据导出")
    p.add_argument("--export-id", required=True)
    p.set_defaults(func=cmd_task_retry_data_export)

    privacy = sub.add_parser("privacy", help="数据权利运维").add_subparsers(
        dest="cmd", required=True
    )
    p = privacy.add_parser("run-purges", help="立即扫描并清理宽限期到期账号")
    p.set_defaults(func=cmd_privacy_run_purges)
    p = privacy.add_parser("purge-user", help="立即清理指定 deletion_pending 账号")
    p.add_argument("--user-id", required=True)
    p.set_defaults(func=cmd_privacy_purge_user)
    p = privacy.add_parser("cleanup-exports", help="清理过期导出文件")
    p.set_defaults(func=cmd_privacy_cleanup_exports)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db, engine = _open_session()
    try:
        args.func(db, args)
        return 0
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
