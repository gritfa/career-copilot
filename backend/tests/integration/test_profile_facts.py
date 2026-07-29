"""事实库：候选确认入库、未确认不可引用、版本化、废止、删除简历的证据脱链。"""

import uuid

from sqlalchemy import select

from app.db.models import FactCandidate, FactEvidence, ProfileFact, Resume, ResumeParse
from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)
from tests.integration.resume_files import make_pdf

PDF_MIME = "application/pdf"


async def login(client, db_factory):
    user = await create_user(db_factory, unique_email())
    cookie = await create_session_for(db_factory, user)
    client.cookies.set(session_cookie(), cookie)
    return user


async def setup_parsed_resume(client) -> tuple[str, list[dict]]:
    up = await client.post(
        "/api/v1/resumes/uploads", files={"file": ("resume.pdf", make_pdf(), PDF_MIME)}
    )
    assert up.status_code == 201, up.text
    created = await client.post(
        "/api/v1/resumes", json={"upload_id": up.json()["upload_id"]}
    )
    assert created.status_code == 202, created.text
    resume_id = created.json()["resume"]["id"]
    parse = await client.get(f"/api/v1/resumes/{resume_id}/parse")
    assert parse.status_code == 200
    return resume_id, parse.json()["candidates"]


def pick(candidates: list[dict], fact_type: str) -> dict:
    return next(c for c in candidates if c["fact_type"] == fact_type)


async def test_unconfirmed_candidates_never_enter_profile_facts(client, db_factory):
    """铁律：解析完成但未确认 → profile_facts 必须为空。"""
    await login(client, db_factory)
    _, candidates = await setup_parsed_resume(client)
    assert len(candidates) > 0

    facts = await client.get("/api/v1/profile/facts")
    assert facts.status_code == 200
    assert facts.json()["items"] == []


async def test_confirm_accept_edit_reject_flow(client, db_factory):
    user = await login(client, db_factory)
    resume_id, candidates = await setup_parsed_resume(client)

    email = pick(candidates, "contact_email")
    skill = pick(candidates, "skill")
    phone = pick(candidates, "contact_phone")

    resp = await client.post(
        f"/api/v1/resumes/{resume_id}/facts/confirm",
        json={
            "decisions": [
                {"candidate_id": email["id"], "action": "accept"},
                {
                    "candidate_id": skill["id"],
                    "action": "edit",
                    "value_json": {"name": "Python", "level": "精通(用户修订)"},
                },
                {"candidate_id": phone["id"], "action": "reject"},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["accepted"], body["edited"], body["rejected"]) == (1, 1, 1)
    assert len(body["facts"]) == 2  # 拒绝的不入库

    for fact in body["facts"]:
        assert fact["status"] == "active"
        assert fact["provenance_type"] == "resume"
        assert fact["confirmed_by_user_at"] is not None

    edited = next(f for f in body["facts"] if f["fact_type"] == "skill")
    assert edited["value_json"]["level"] == "精通(用户修订)"

    # 证据：resume_id + span 定位 + 引文哈希 + 最小摘录
    async with db_factory() as db:
        evidences = (
            (
                await db.execute(
                    select(FactEvidence)
                    .join(ProfileFact, ProfileFact.id == FactEvidence.profile_fact_id)
                    .where(ProfileFact.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(evidences) == 2
        for ev in evidences:
            assert str(ev.resume_id) == resume_id
            assert len(ev.evidence_hash) == 64
            assert "span_start" in ev.source_locator
            assert 0 < len(ev.display_excerpt) <= 200

    # 拒绝的候选不产生任何 profile_fact
    facts = await client.get("/api/v1/profile/facts")
    types = {f["fact_type"] for f in facts.json()["items"]}
    assert "contact_phone" not in types

    # 重复决策同一候选 → 409
    resp = await client.post(
        f"/api/v1/resumes/{resume_id}/facts/confirm",
        json={"decisions": [{"candidate_id": email["id"], "action": "accept"}]},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CANDIDATE_ALREADY_DECIDED"


async def test_confirm_edit_requires_value_and_unknown_candidate_404(client, db_factory):
    await login(client, db_factory)
    resume_id, candidates = await setup_parsed_resume(client)

    resp = await client.post(
        f"/api/v1/resumes/{resume_id}/facts/confirm",
        json={"decisions": [{"candidate_id": candidates[0]["id"], "action": "edit"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "EDIT_VALUE_REQUIRED"

    resp = await client.post(
        f"/api/v1/resumes/{resume_id}/facts/confirm",
        json={"decisions": [{"candidate_id": str(uuid.uuid4()), "action": "accept"}]},
    )
    assert resp.status_code == 404


async def test_cross_user_cannot_confirm_others_candidates(client, db_factory, make_client):
    """用户 B 用自己的简历 ID 也确认不了 A 的候选（候选归属校验）。"""
    await login(client, db_factory)
    _, candidates_a = await setup_parsed_resume(client)

    other = make_client()
    async with other:
        other_user = await create_user(db_factory, unique_email("other"))
        other.cookies.set(session_cookie(), await create_session_for(db_factory, other_user))
        resume_b, _ = await setup_parsed_resume(other)
        resp = await other.post(
            f"/api/v1/resumes/{resume_b}/facts/confirm",
            json={"decisions": [{"candidate_id": candidates_a[0]["id"], "action": "accept"}]},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    # A 的候选未被动过
    facts = await client.get("/api/v1/profile/facts")
    assert facts.json()["items"] == []


async def test_manual_fact_crud_and_versioning(client, db_factory):
    await login(client, db_factory)

    # 受保护属性任何入口都不允许写入
    resp = await client.post(
        "/api/v1/profile/facts", json={"fact_type": "gender", "value_json": {"value": "男"}}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PROTECTED_ATTRIBUTE"

    created = await client.post(
        "/api/v1/profile/facts",
        json={"fact_type": "skill", "value_json": {"name": "Kubernetes", "level": "使用级"}},
    )
    assert created.status_code == 201, created.text
    fact_v1 = created.json()
    assert fact_v1["provenance_type"] == "user_answer"

    # PATCH：生成新版本，旧版本 superseded 并指向新版本
    patched = await client.patch(
        f"/api/v1/profile/facts/{fact_v1['id']}",
        json={"value_json": {"name": "Kubernetes", "level": "熟练"}},
    )
    assert patched.status_code == 200, patched.text
    fact_v2 = patched.json()
    assert fact_v2["id"] != fact_v1["id"]
    assert fact_v2["value_json"]["level"] == "熟练"

    history = await client.get("/api/v1/profile/facts?include_history=true")
    by_id = {f["id"]: f for f in history.json()["items"]}
    assert by_id[fact_v1["id"]]["status"] == "superseded"
    assert by_id[fact_v1["id"]]["superseded_by_id"] == fact_v2["id"]
    assert by_id[fact_v2["id"]]["status"] == "active"

    # 默认列表只看 active
    active = await client.get("/api/v1/profile/facts")
    assert [f["id"] for f in active.json()["items"]] == [fact_v2["id"]]

    # 旧版本不能再 PATCH / DELETE
    resp = await client.patch(
        f"/api/v1/profile/facts/{fact_v1['id']}", json={"value_json": {"name": "x"}}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "FACT_NOT_ACTIVE"

    # DELETE：废止当前版本
    revoked = await client.delete(f"/api/v1/profile/facts/{fact_v2['id']}")
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert (await client.get("/api/v1/profile/facts")).json()["items"] == []

    resp = await client.delete(f"/api/v1/profile/facts/{fact_v2['id']}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "FACT_ALREADY_REVOKED"


async def test_cross_user_fact_access_is_404(client, db_factory, make_client):
    await login(client, db_factory)
    created = await client.post(
        "/api/v1/profile/facts", json={"fact_type": "skill", "value_json": {"name": "Go"}}
    )
    fact_id = created.json()["id"]

    other = make_client()
    async with other:
        other_user = await create_user(db_factory, unique_email("other"))
        other.cookies.set(session_cookie(), await create_session_for(db_factory, other_user))
        for method in ("PATCH", "DELETE"):
            resp = await other.request(
                method,
                f"/api/v1/profile/facts/{fact_id}",
                json={"value_json": {"name": "hack"}} if method == "PATCH" else None,
            )
            assert resp.status_code == 404, f"{method}: {resp.status_code}"


async def test_delete_resume_precheck_and_async_cleanup(client, db_factory):
    """删除简历：依赖预检查 → 确认 → 异步清理；已确认事实保留、证据脱链。"""
    user = await login(client, db_factory)
    resume_id, candidates = await setup_parsed_resume(client)
    email = pick(candidates, "contact_email")
    await client.post(
        f"/api/v1/resumes/{resume_id}/facts/confirm",
        json={"decisions": [{"candidate_id": email["id"], "action": "accept"}]},
    )

    async with db_factory() as db:
        resume_row = (
            await db.execute(select(Resume).where(Resume.id == uuid.UUID(resume_id)))
        ).scalar_one()
        storage_key = resume_row.storage_key
        extracted_key = (
            await db.execute(
                select(ResumeParse.extracted_text_storage_key).where(
                    ResumeParse.resume_id == resume_row.id
                )
            )
        ).scalar_one()

    from app.integrations.storage import get_storage

    assert get_storage().exists(storage_key)
    assert get_storage().exists(extracted_key)

    # 未确认 → 409 + 依赖预检查结果
    resp = await client.delete(f"/api/v1/resumes/{resume_id}")
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "CONFIRMATION_REQUIRED"
    assert err["details"]["active_profile_facts_with_evidence"] == 1

    # 确认删除 → 202，eager 清理任务同步完成
    resp = await client.delete(f"/api/v1/resumes/{resume_id}?confirm=true")
    assert resp.status_code == 202, resp.text

    # 存储对象（原始文件 + 提取文本）已删除
    assert not get_storage().exists(storage_key)
    assert not get_storage().exists(extracted_key)

    async with db_factory() as db:
        resume_row = (
            await db.execute(select(Resume).where(Resume.id == uuid.UUID(resume_id)))
        ).scalar_one()
        assert resume_row.status == "deleted"
        assert resume_row.deleted_at is not None
        # 解析与候选行已清除
        parses = (
            await db.execute(
                select(ResumeParse).where(ResumeParse.resume_id == resume_row.id)
            )
        ).scalars().all()
        assert parses == []
        orphans = (await db.execute(select(FactCandidate))).scalars().all()
        assert orphans == []
        # 已确认事实保留，证据与文件脱链但保留哈希/摘录
        facts = (
            (
                await db.execute(
                    select(ProfileFact).where(
                        ProfileFact.user_id == user.id, ProfileFact.status == "active"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(facts) == 1
        evidence = (
            await db.execute(
                select(FactEvidence).where(FactEvidence.profile_fact_id == facts[0].id)
            )
        ).scalar_one()
        assert evidence.resume_id is None
        assert len(evidence.evidence_hash) == 64

    # 墓碑行对 API 不可见
    assert (await client.get(f"/api/v1/resumes/{resume_id}")).status_code == 404
    assert (await client.get("/api/v1/resumes")).json()["items"] == []
