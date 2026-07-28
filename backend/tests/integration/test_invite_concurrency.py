"""邀请码并发超用：asyncio.gather 并发 verify 不得超过 max_uses。"""

import asyncio

from sqlalchemy import func, select

from tests.integration.conftest import (
    create_invite,
    mailpit_find_token,
    request_magic_link,
    unique_email,
)


async def test_concurrent_verify_cannot_exceed_max_uses(client, db_factory):
    max_uses = 2
    total = 5
    await create_invite(db_factory, "conc-code", max_uses=max_uses)

    emails = [unique_email(f"conc{i}") for i in range(total)]
    for email in emails:
        resp = await request_magic_link(client, email, "conc-code")
        assert resp.status_code == 202
    tokens = [mailpit_find_token(email) for email in emails]

    responses = await asyncio.gather(
        *[
            client.post("/api/v1/auth/magic-links/verify", json={"token": t})
            for t in tokens
        ]
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses.count(200) == max_uses, statuses
    assert statuses.count(409) == total - max_uses, statuses
    for r in responses:
        if r.status_code == 409:
            assert r.json()["error"]["code"] == "INVITE_EXHAUSTED"

    from app.db.models import Invite, User

    async with db_factory() as db:
        invite = (await db.execute(select(Invite))).scalars().one()
        assert invite.used_count == max_uses  # DB CheckConstraint 也保证不可超用
        user_count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        assert user_count == max_uses
