"""阶段 11 P-1 第 1/2 项：推荐读取可见性 + 历史跨用户推荐清理（集成测试）。

产品规则（与 /jobs 系列同一规则，docs/15 P-1）：
- 推荐指向的岗位转私有/属主注销后，非属主的历史推荐不得继续暴露岗位内容：
  列表不返回、详情/反馈/定制简历一律 404（不泄露存在性）；
- 属主本人的私有岗位推荐照常可见；
- 迁移 e5a7c9d2b4f6 清除存量泄露行（级联清 match_components 等），
  测试直接导入迁移模块里的同一条 PURGE_SQL 验证语义（单一事实源）。
"""

import importlib.util
from pathlib import Path

from sqlalchemy import select, text, update

from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)
from tests.integration.test_matching_pipeline import (
    create_facts,
    create_job,
    create_plan,
    fetch_recommendations,
    run_matching,
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260730_e5a7c9d2b4f6_purge_cross_user_recommendations.py"
)


def _load_purge_sql() -> str:
    spec = importlib.util.spec_from_file_location("purge_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PURGE_SQL


async def _login_user_with_rec(make_client, db_factory, prefix: str):
    """已登录用户 + 一条真实匹配管道产出的推荐（岗位初始为 global）。"""
    user = await create_user(db_factory, unique_email(prefix))
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user)
    job_id = await create_job(db_factory)
    run_matching(plan.id)
    recs = await fetch_recommendations(db_factory, plan.id)
    rec = next(r for r in recs if r.canonical_job_id == job_id)
    client = make_client()
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))
    return user, plan, job_id, rec, client


async def _set_job_private(db_factory, job_id, owner_user_id) -> None:
    """把岗位翻转为他人私有（模拟：岗位转私有/历史泄露的存量状态）。"""
    from app.db.models import CanonicalJob

    async with db_factory() as db:
        await db.execute(
            update(CanonicalJob)
            .where(CanonicalJob.id == job_id)
            .values(visibility="private", owner_user_id=owner_user_id)
        )
        await db.commit()


# ---------------- 1. 岗位转他人私有后：列表/详情/反馈/定制简历全部不可见 ----------------


async def test_read_paths_hide_recommendation_of_others_private_job(
    make_client, db_factory
):
    _user, _plan, job_id, rec, client = await _login_user_with_rec(
        make_client, db_factory, "rec-vis-b"
    )
    other = await create_user(db_factory, unique_email("rec-vis-owner"))

    # global 时本人可见（前置校验，防测试空转）
    resp = await client.get("/api/v1/recommendations")
    assert resp.status_code == 200
    assert str(rec.id) in [item["id"] for item in resp.json()["items"]]

    await _set_job_private(db_factory, job_id, other.id)

    # 列表不再返回
    resp = await client.get("/api/v1/recommendations")
    assert resp.status_code == 200
    assert str(rec.id) not in [item["id"] for item in resp.json()["items"]]
    # 详情 404（与不存在无差别）
    resp = await client.get(f"/api/v1/recommendations/{rec.id}")
    assert resp.status_code == 404
    # 反馈 404
    resp = await client.post(
        f"/api/v1/recommendations/{rec.id}/feedback",
        json={"sentiment": "interested"},
    )
    assert resp.status_code == 404
    # 定制简历草稿 404（不得再基于不可见岗位生成简历）
    resp = await client.post(f"/api/v1/recommendations/{rec.id}/resume-drafts")
    assert resp.status_code == 404


# ---------------- 2. 属主本人的私有岗位推荐照常可见 ----------------


async def test_owner_still_sees_own_private_job_recommendation(
    make_client, db_factory
):
    user, _plan, job_id, rec, client = await _login_user_with_rec(
        make_client, db_factory, "rec-vis-a"
    )
    await _set_job_private(db_factory, job_id, user.id)

    resp = await client.get("/api/v1/recommendations")
    assert resp.status_code == 200
    assert str(rec.id) in [item["id"] for item in resp.json()["items"]]
    resp = await client.get(f"/api/v1/recommendations/{rec.id}")
    assert resp.status_code == 200


# ---------------- 3. 迁移 PURGE_SQL：删泄露行、级联清组件、不误删合法行 ----------------


async def test_purge_sql_deletes_leaked_keeps_legit(make_client, db_factory):
    from app.db.models import MatchComponent, Recommendation

    # 泄露行：B 的推荐指向他人私有岗位
    _b, _plan_b, job_b, rec_leaked, _client_b = await _login_user_with_rec(
        make_client, db_factory, "purge-b"
    )
    other = await create_user(db_factory, unique_email("purge-owner"))
    await _set_job_private(db_factory, job_b, other.id)

    # 合法行 1：A 的推荐指向 A 自己的私有岗位
    a, _plan_a, job_a, rec_own_private, _client_a = await _login_user_with_rec(
        make_client, db_factory, "purge-a"
    )
    await _set_job_private(db_factory, job_a, a.id)

    # 合法行 2：C 的推荐指向 global 岗位
    _c, _plan_c, _job_c, rec_global, _client_c = await _login_user_with_rec(
        make_client, db_factory, "purge-c"
    )

    async with db_factory() as db:
        leaked_components = (
            (
                await db.execute(
                    select(MatchComponent).where(
                        MatchComponent.recommendation_id == rec_leaked.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert leaked_components, "前置：泄露推荐应有评分组件"

        await db.execute(text(_load_purge_sql()))
        await db.commit()

    async with db_factory() as db:
        remaining = {
            row
            for row in (
                await db.execute(
                    select(Recommendation.id).where(
                        Recommendation.id.in_(
                            [rec_leaked.id, rec_own_private.id, rec_global.id]
                        )
                    )
                )
            ).scalars()
        }
        assert rec_leaked.id not in remaining, "泄露行应被清除"
        assert rec_own_private.id in remaining, "属主本人的私有岗位推荐不得误删"
        assert rec_global.id in remaining, "global 岗位推荐不得误删"
        leftover = (
            (
                await db.execute(
                    select(MatchComponent).where(
                        MatchComponent.recommendation_id == rec_leaked.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert leftover == [], "泄露行的评分组件应随外键级联清除"


# ---------------- 4. 属主注销（owner_user_id 为 NULL 的私有岗位）同样清除/隐藏 ----------------


async def test_private_job_with_null_owner_hidden_and_purged(make_client, db_factory):
    from app.db.models import Recommendation

    _user, _plan, job_id, rec, client = await _login_user_with_rec(
        make_client, db_factory, "rec-vis-null"
    )
    await _set_job_private(db_factory, job_id, None)

    resp = await client.get(f"/api/v1/recommendations/{rec.id}")
    assert resp.status_code == 404

    async with db_factory() as db:
        await db.execute(text(_load_purge_sql()))
        await db.commit()
        gone = (
            await db.execute(select(Recommendation).where(Recommendation.id == rec.id))
        ).scalar_one_or_none()
        assert gone is None, "owner 为 NULL 的私有岗位推荐应被清除（IS DISTINCT FROM 语义）"
