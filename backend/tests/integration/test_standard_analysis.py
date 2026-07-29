"""单模型标准分析集成测试（真实 PostgreSQL + Redis，Celery eager）。

覆盖：手动触发 → 任务执行 → 报告入库与查询、证据绑定与 not_verified 标注、
费用账本、每日手动额度、进行中幂等、失败降级不伪装完成、
证据校验拦截虚构、越权 404、日志无正文。
"""

import json
import uuid

import structlog.testing
from sqlalchemy import select

from tests.integration.conftest import create_session_for, create_user, session_cookie
from tests.integration.test_matching_pipeline import (
    create_facts,
    create_job,
    create_plan,
    run_matching,
)


async def setup_recommendations(db_factory, client, email, n_jobs=1, **plan_kw):
    user = await create_user(db_factory, email)
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user, **plan_kw)
    for i in range(n_jobs):
        await create_job(db_factory, title=f"Python 后端开发工程师 {i}")
    run_matching(plan.id)
    token = await create_session_for(db_factory, user)
    client.cookies.set(session_cookie(), token)
    items = (await client.get("/api/v1/recommendations")).json()["items"]
    return user, plan, items


async def get_run_row(db_factory, run_id):
    from app.db.models import AgentRun

    async with db_factory() as db:
        return (
            await db.execute(select(AgentRun).where(AgentRun.id == uuid.UUID(run_id)))
        ).scalar_one()


# ---------------- 主路径：触发 → 完成 → 报告 ----------------


async def test_trigger_analysis_completes_with_evidence_bound_report(db_factory, client):
    user, _plan, items = await setup_recommendations(
        db_factory, client, "ana-main@cc-integration.dev"
    )
    rec_id = items[0]["id"]

    resp = await client.post(f"/api/v1/recommendations/{rec_id}/deep-analysis")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    # eager 模式下任务同步完成
    assert body["status"] == "completed"
    assert body["trigger"] == "manual"
    assert body["provider"] == "synthetic"
    assert body["model_id"] == "synthetic-analysis@1"
    assert body["verified"] is False  # 合成结果如实标注 not_verified
    assert body["graph_version"] == "single_model_v1"
    assert body["output_schema_version"] == "std_analysis_v1"

    # GET /agent-runs/{id}：只有状态与最终报告，无内部对话字段
    run = (await client.get(f"/api/v1/agent-runs/{body['id']}")).json()
    assert run["status"] == "completed"
    report = run["report"]
    assert report["schema_version"] == "std_analysis_v1"
    assert report["overall_summary"]

    # 证据绑定：结论引用的事实 ID 必须真实存在于该用户的已确认事实
    from app.db.models import ProfileFact

    async with db_factory() as db:
        fact_ids = {
            str(fid)
            for fid in (
                await db.execute(
                    select(ProfileFact.id).where(ProfileFact.user_id == user.id)
                )
            ).scalars()
        }
    assert report["strengths"], "标准分析应产出匹配亮点"
    for claim in report["strengths"]:
        assert claim["profile_fact_ids"], "亮点必须绑定事实引用"
        assert set(claim["profile_fact_ids"]) <= fact_ids
        assert claim["job_span"], "亮点必须带岗位原文片段"
    for suggestion in report["resume_suggestions"]:
        assert suggestion["based_on_fact_ids"] or (
            suggestion["uncertainty"] == "insufficient_evidence"
        )

    # 任务行：指纹已写入、费用计数落库
    row = await get_run_row(db_factory, body["id"])
    assert row.input_fingerprint != "pending" and len(row.input_fingerprint) == 64
    assert row.cost_tokens_in > 0 and row.cost_tokens_out > 0
    assert row.final_report_json is not None

    # 费用账本：llm_analysis 记账（合成实现零费用也要记）
    from app.db.models import UsageLedger

    async with db_factory() as db:
        ledger = (
            (
                await db.execute(
                    select(UsageLedger).where(UsageLedger.operation_type == "llm_analysis")
                )
            )
            .scalars()
            .all()
        )
    assert len(ledger) == 1
    assert ledger[0].provider == "synthetic"
    assert ledger[0].user_id == user.id
    assert float(ledger[0].amount_estimated) == 0.0

    # 列表接口
    analyses = (await client.get(f"/api/v1/recommendations/{rec_id}/analyses")).json()
    assert len(analyses["items"]) == 1
    assert analyses["items"][0]["id"] == body["id"]


async def test_same_input_same_fingerprint_and_report(db_factory, client):
    """确定性：同一推荐两次分析，输入指纹与报告完全一致（同输入同输出）。"""
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "ana-determ@cc-integration.dev"
    )
    rec_id = items[0]["id"]
    first = (await client.post(f"/api/v1/recommendations/{rec_id}/deep-analysis")).json()
    second = (await client.post(f"/api/v1/recommendations/{rec_id}/deep-analysis")).json()
    row1 = await get_run_row(db_factory, first["id"])
    row2 = await get_run_row(db_factory, second["id"])
    assert row1.input_fingerprint == row2.input_fingerprint
    assert row1.final_report_json == row2.final_report_json


# ---------------- 额度与幂等 ----------------


async def test_manual_daily_quota_3_then_429(db_factory, client):
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "ana-quota@cc-integration.dev", n_jobs=4
    )
    assert len(items) == 4
    for item in items[:3]:
        resp = await client.post(f"/api/v1/recommendations/{item['id']}/deep-analysis")
        assert resp.status_code == 202, resp.text
    resp = await client.post(f"/api/v1/recommendations/{items[3]['id']}/deep-analysis")
    assert resp.status_code == 429
    err = resp.json()["error"]
    assert err["code"] == "RATE_LIMITED"
    assert err["details"] == {"limit": 3, "used": 3}


async def test_inflight_run_reused_without_new_quota(db_factory, client, monkeypatch):
    """进行中的任务：重复触发幂等返回同一 run，不新建、不扣额度。"""
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "ana-inflight@cc-integration.dev"
    )
    rec_id = items[0]["id"]
    # 让任务不执行（模拟排队中）：dispatch 变 no-op
    monkeypatch.setattr("app.agents.router.dispatch_task", lambda *a, **k: None)
    first = (await client.post(f"/api/v1/recommendations/{rec_id}/deep-analysis")).json()
    assert first["status"] == "queued"
    second = (await client.post(f"/api/v1/recommendations/{rec_id}/deep-analysis")).json()
    assert second["id"] == first["id"]  # 幂等复用

    from app.db.models import AgentRun

    async with db_factory() as db:
        count = len((await db.execute(select(AgentRun))).scalars().all())
    assert count == 1


# ---------------- 失败路径：不伪装完成，基础结果仍可用 ----------------


async def test_model_failure_marks_failed_and_base_results_survive(
    db_factory, client, monkeypatch
):
    from app.integrations.llm_gateway import LLMError, LLMRequest

    class AlwaysFailAdapter:
        provider = "synthetic"
        model_id = "synthetic-analysis@1"
        configured = True

        def complete(self, request: LLMRequest):
            raise LLMError("model down")

    monkeypatch.setattr("app.integrations.llm_gateway.time.sleep", lambda *_: None)
    monkeypatch.setattr(
        "app.integrations.llm_gateway.get_llm_adapter", lambda: AlwaysFailAdapter()
    )
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "ana-fail@cc-integration.dev"
    )
    rec_id = items[0]["id"]
    body = (await client.post(f"/api/v1/recommendations/{rec_id}/deep-analysis")).json()
    assert body["status"] == "failed"
    assert body["error_code"] == "MODEL_UNAVAILABLE"
    assert body["report"] is None  # 失败绝不伪装完整报告

    # 降级语义：规则 + 向量基础匹配结果不受影响
    detail = await client.get(f"/api/v1/recommendations/{rec_id}")
    assert detail.status_code == 200
    assert len(detail.json()["components"]) == 6


async def test_fabricated_fact_ids_blocked_by_validation(db_factory, client, monkeypatch):
    """模型虚构事实引用 → validating 阶段拦截，任务明确失败。"""
    from app.integrations.llm_gateway import LLMRawResponse, LLMRequest

    fabricated = {
        "schema_version": "std_analysis_v1",
        "overall_summary": "看起来很匹配",
        "strengths": [
            {
                "claim": "具备 Kubernetes 专家经验",
                "profile_fact_ids": [str(uuid.uuid4())],  # 输入中不存在的事实
                "job_span": "Python 后端",
                "strength": "strong",
                "uncertainty": None,
            }
        ],
        "gaps": [],
        "risks": [],
        "resume_suggestions": [],
    }

    class FabricatingAdapter:
        provider = "synthetic"
        model_id = "synthetic-analysis@1"
        configured = True

        def complete(self, request: LLMRequest):
            return LLMRawResponse(
                content=json.dumps(fabricated, ensure_ascii=False),
                tokens_in=10,
                tokens_out=10,
            )

    monkeypatch.setattr(
        "app.integrations.llm_gateway.get_llm_adapter", lambda: FabricatingAdapter()
    )
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "ana-fabricate@cc-integration.dev"
    )
    body = (
        await client.post(f"/api/v1/recommendations/{items[0]['id']}/deep-analysis")
    ).json()
    assert body["status"] == "failed"
    assert body["error_code"] == "EVIDENCE_VALIDATION_FAILED"
    assert body["report"] is None


# ---------------- 越权（IDOR） ----------------


async def test_cross_user_analysis_access_404(db_factory, client, make_client):
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "ana-owner@cc-integration.dev"
    )
    rec_id = items[0]["id"]
    run = (await client.post(f"/api/v1/recommendations/{rec_id}/deep-analysis")).json()

    intruder = await create_user(db_factory, "ana-intruder@cc-integration.dev")
    other = make_client()
    other.cookies.set(session_cookie(), await create_session_for(db_factory, intruder))
    try:
        assert (
            await other.post(f"/api/v1/recommendations/{rec_id}/deep-analysis")
        ).status_code == 404
        assert (await other.get(f"/api/v1/agent-runs/{run['id']}")).status_code == 404
        assert (
            await other.get(f"/api/v1/recommendations/{rec_id}/analyses")
        ).status_code == 404
    finally:
        await other.aclose()


# ---------------- 日志边界 ----------------


async def test_analysis_logs_contain_no_fact_or_job_content(db_factory, client):
    """分析全链路日志：岗位正文、事实内容、报告正文绝不入日志。"""
    user = await create_user(db_factory, "ana-logs@cc-integration.dev")
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user)
    canary = "ANALYSISCANARY机密岗位职责七二一"
    await create_job(db_factory, description=f"负责 FastAPI 服务开发。{canary}")
    run_matching(plan.id)
    token = await create_session_for(db_factory, user)
    client.cookies.set(session_cookie(), token)
    items = (await client.get("/api/v1/recommendations")).json()["items"]

    with structlog.testing.capture_logs() as logs:
        resp = await client.post(
            f"/api/v1/recommendations/{items[0]['id']}/deep-analysis"
        )
    assert resp.json()["status"] == "completed"
    dumped = repr(logs)
    assert canary not in dumped
    assert "星辰科技有限公司" not in dumped  # 事实内容不入日志
    assert any(e.get("event") == "analysis_run_completed" for e in logs)
    assert any(e.get("event") == "llm_completed" for e in logs)
