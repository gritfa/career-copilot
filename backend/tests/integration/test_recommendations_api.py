"""推荐查询 / 反馈 API 集成测试（真实 PG + Redis）。

覆盖：列表筛选与游标分页、详情（分项+证据+硬条件+风险+来源链接）、
反馈写入/替换/撤回、学习偏好累计与重置打通、越权 404。
"""

from sqlalchemy import select

from tests.integration.conftest import create_session_for, create_user, session_cookie
from tests.integration.test_matching_pipeline import (
    create_facts,
    create_job,
    create_plan,
    run_matching,
)


async def setup_user_with_recommendations(db_factory, client, email, n_jobs=2, **plan_kw):
    user = await create_user(db_factory, email)
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user, **plan_kw)
    for i in range(n_jobs):
        await create_job(db_factory, title=f"Python 后端开发工程师 {i}")
    run_matching(plan.id)
    token = await create_session_for(db_factory, user)
    client.cookies.set(session_cookie(), token)
    return user, plan


async def get_learned_weights(db_factory, user_id):
    from app.db.models import LearnedPreference

    async with db_factory() as db:
        row = (
            await db.execute(
                select(LearnedPreference).where(
                    LearnedPreference.user_id == user_id,
                    LearnedPreference.status == "active",
                )
            )
        ).scalar_one_or_none()
        return dict(row.weights_json) if row else None


# ---------------- 列表 / 详情 ----------------


async def test_list_and_detail_with_evidence(db_factory, client):
    user, plan = await setup_user_with_recommendations(
        db_factory, client, "rec-list@cc-integration.dev"
    )
    resp = await client.get("/api/v1/recommendations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    item = body["items"][0]
    assert item["grade"] in ("high", "potential")  # low 默认隐藏
    assert item["job_title"]

    # 按方案/日期/等级筛选
    resp = await client.get(
        "/api/v1/recommendations",
        params={
            "plan_id": str(plan.id),
            "date": item["recommended_on"],
            "grade": item["grade"],
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) >= 1

    # 游标分页
    resp = await client.get("/api/v1/recommendations", params={"limit": 1})
    page1 = resp.json()
    assert len(page1["items"]) == 1 and page1["next_cursor"]
    resp = await client.get(
        "/api/v1/recommendations", params={"limit": 1, "cursor": page1["next_cursor"]}
    )
    page2 = resp.json()
    assert len(page2["items"]) == 1
    assert page2["items"][0]["id"] != page1["items"][0]["id"]

    # 详情：分项+证据+硬条件明细+来源链接
    detail = (await client.get(f"/api/v1/recommendations/{item['id']}")).json()
    assert len(detail["hard_conditions"]) == 8
    assert all(h["evidence"] for h in detail["hard_conditions"])
    assert len(detail["components"]) == 6
    for comp in detail["components"]:
        assert comp["evidence_refs"] or comp["uncertainty"] == "insufficient_evidence"
    assert detail["source_links"] and detail["source_links"][0]["url"]
    assert detail["scoring_version"] == "score_v1"
    assert detail["versions"]["embedding_model_id"] == "det-hash-768@1"


async def test_risk_signals_shown_separately(db_factory, client):
    """低置信外包信号：进 uncertain 队列并在详情 risks 中展示（不混入能力分）。"""
    user = await create_user(db_factory, "rec-risk@cc-integration.dev")
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user)
    signals = [
        {
            "code": "POSSIBLE_OUTSOURCING",
            "keyword": "驻场",
            "evidence": "description: …需要驻场…",
            "confidence": 0.7,
        }
    ]
    await create_job(db_factory, outsourcing_signals=signals)
    run_matching(plan.id)
    token = await create_session_for(db_factory, user)
    client.cookies.set(session_cookie(), token)

    resp = await client.get(
        "/api/v1/recommendations", params={"hard_filter_status": "uncertain"}
    )
    items = resp.json()["items"]
    assert len(items) == 1
    detail = (await client.get(f"/api/v1/recommendations/{items[0]['id']}")).json()
    assert detail["risks"][0]["code"] == "POSSIBLE_OUTSOURCING"
    assert detail["risks"][0]["confidence"] == 0.7


# ---------------- 反馈与学习偏好 ----------------


async def test_feedback_write_replace_withdraw_and_learning(db_factory, client):
    user, _plan = await setup_user_with_recommendations(
        db_factory, client, "rec-fb@cc-integration.dev", n_jobs=2
    )
    items = (await client.get("/api/v1/recommendations")).json()["items"]
    rec_a, rec_b = items[0]["id"], items[1]["id"]

    # 不感兴趣必须带原因
    resp = await client.post(
        f"/api/v1/recommendations/{rec_a}/feedback", json={"sentiment": "not_interested"}
    )
    assert resp.status_code == 422

    # 写入两条 not_interested:salary → 学习偏好累计到 2
    for rec_id in (rec_a, rec_b):
        resp = await client.post(
            f"/api/v1/recommendations/{rec_id}/feedback",
            json={"sentiment": "not_interested", "reason_code": "salary", "note": "太低了"},
        )
        assert resp.status_code == 201, resp.text
    weights = await get_learned_weights(db_factory, user.id)
    assert weights == {"not_interested:salary": 2}

    # 替换：rec_a 改为 interested → salary 计数回退到 1
    resp = await client.post(
        f"/api/v1/recommendations/{rec_a}/feedback", json={"sentiment": "interested"}
    )
    assert resp.status_code == 201
    weights = await get_learned_weights(db_factory, user.id)
    assert weights == {"not_interested:salary": 1, "interested:none": 1}

    # 列表回显反馈
    items = (await client.get("/api/v1/recommendations")).json()["items"]
    by_id = {i["id"]: i for i in items}
    assert by_id[rec_a]["feedback_sentiment"] == "interested"
    assert by_id[rec_b]["feedback_sentiment"] == "not_interested"

    # 撤回 rec_b → salary 计数清零（键移除）
    resp = await client.delete(f"/api/v1/recommendations/{rec_b}/feedback")
    assert resp.status_code == 200
    weights = await get_learned_weights(db_factory, user.id)
    assert weights == {"interested:none": 1}
    # 再撤回同一条 → 404
    resp = await client.delete(f"/api/v1/recommendations/{rec_b}/feedback")
    assert resp.status_code == 404

    # 反馈 note 不进审计（审计只有 reason_code）
    from app.db.models import AuditEvent

    async with db_factory() as db:
        events = (
            (
                await db.execute(
                    select(AuditEvent).where(
                        AuditEvent.action == "recommendation_feedback_submitted"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert events
    for event in events:
        assert "太低了" not in (event.reason_code or "")


async def test_reset_learned_then_new_feedback_starts_fresh(db_factory, client):
    """reset-learned 打通：重置后旧计数不复活，新反馈从零累计。"""
    user, _plan = await setup_user_with_recommendations(
        db_factory, client, "rec-reset@cc-integration.dev", n_jobs=2
    )
    items = (await client.get("/api/v1/recommendations")).json()["items"]
    rec_a, rec_b = items[0]["id"], items[1]["id"]
    await client.post(
        f"/api/v1/recommendations/{rec_a}/feedback",
        json={"sentiment": "not_interested", "reason_code": "outsourcing"},
    )
    assert await get_learned_weights(db_factory, user.id) == {"not_interested:outsourcing": 1}

    resp = await client.post("/api/v1/preferences/reset-learned")
    assert resp.status_code == 200
    assert await get_learned_weights(db_factory, user.id) == {}

    # 重置后的新反馈：从零开始，历史反馈不复活
    await client.post(
        f"/api/v1/recommendations/{rec_b}/feedback",
        json={"sentiment": "not_interested", "reason_code": "salary"},
    )
    assert await get_learned_weights(db_factory, user.id) == {"not_interested:salary": 1}


# ---------------- 越权（IDOR） ----------------


async def test_cross_user_access_returns_404(db_factory, client, make_client):
    _user, _plan = await setup_user_with_recommendations(
        db_factory, client, "rec-owner@cc-integration.dev", n_jobs=1
    )
    rec_id = (await client.get("/api/v1/recommendations")).json()["items"][0]["id"]

    intruder = await create_user(db_factory, "rec-intruder@cc-integration.dev")
    other = make_client()
    other.cookies.set(session_cookie(), await create_session_for(db_factory, intruder))
    try:
        # 他人推荐：详情/反馈/撤回一律 404（不泄露存在性）
        assert (await other.get(f"/api/v1/recommendations/{rec_id}")).status_code == 404
        resp = await other.post(
            f"/api/v1/recommendations/{rec_id}/feedback", json={"sentiment": "interested"}
        )
        assert resp.status_code == 404
        assert (
            await other.delete(f"/api/v1/recommendations/{rec_id}/feedback")
        ).status_code == 404
        # 他人列表看不到我的推荐
        assert (await other.get("/api/v1/recommendations")).json()["items"] == []
    finally:
        await other.aclose()
