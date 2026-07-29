"""求职方案 API 集成测试（真实 PG/Redis）。

覆盖：CRUD、字段校验（CNY/薪资区间/城市/办公方式/外包默认值）、
3 个 active 上限（API 顺序 + DB 层并发）、公司偏好、学习偏好重置、跨用户越权。
"""

import threading
import uuid

import psycopg
from sqlalchemy import select

from app.db.models import LearnedPreference, SearchPlan
from tests.integration.conftest import (
    TEST_SYNC_DSN,
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)


async def login(client, db_factory):
    user = await create_user(db_factory, unique_email())
    cookie = await create_session_for(db_factory, user)
    client.cookies.set(session_cookie(), cookie)
    return user


def plan_payload(**overrides) -> dict:
    payload = {
        "name": "Python 后端-北京",
        "role_family": "backend_python",
        "city_codes": ["110100", "330100"],
        "work_modes": ["onsite", "hybrid"],
        "minimum_monthly_salary": 20000,
        "target_monthly_salary": 30000,
        "salary_months_preference": 13,
    }
    payload.update(overrides)
    return payload


# ---------------- 创建与校验 ----------------


async def test_create_plan_defaults_and_link_out(client, db_factory):
    await login(client, db_factory)
    resp = await client.post("/api/v1/search-plans", json=plan_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["salary_currency"] == "CNY"
    assert body["minimum_match_score"] == 65  # 默认 65
    assert body["allow_outsourcing"] is False  # 外包默认关闭
    # BOSS 降级：每个城市一个搜索跳转 URL（link-out，不采集）
    assert {u["city_code"] for u in body["link_out_urls"]} == {"110100", "330100"}
    assert all(u["url"].startswith("https://www.zhipin.com/") for u in body["link_out_urls"])


async def test_salary_validation_cny_only_and_min_le_target(client, db_factory):
    await login(client, db_factory)
    # 非 CNY 拒绝
    resp = await client.post(
        "/api/v1/search-plans", json=plan_payload(salary_currency="USD")
    )
    assert resp.status_code == 422
    # minimum > target 拒绝
    resp = await client.post(
        "/api/v1/search-plans",
        json=plan_payload(minimum_monthly_salary=30000, target_monthly_salary=20000),
    )
    assert resp.status_code == 422
    # 负数/零拒绝
    resp = await client.post(
        "/api/v1/search-plans", json=plan_payload(minimum_monthly_salary=0)
    )
    assert resp.status_code == 422


async def test_city_and_work_mode_and_role_validation(client, db_factory):
    await login(client, db_factory)
    # 六城之外的行政区码拒绝
    resp = await client.post(
        "/api/v1/search-plans", json=plan_payload(city_codes=["420100"])
    )
    assert resp.status_code == 422
    resp = await client.post("/api/v1/search-plans", json=plan_payload(city_codes=[]))
    assert resp.status_code == 422
    resp = await client.post(
        "/api/v1/search-plans", json=plan_payload(work_modes=["freelance"])
    )
    assert resp.status_code == 422
    resp = await client.post(
        "/api/v1/search-plans", json=plan_payload(role_family="product_manager")
    )
    assert resp.status_code == 422


async def test_patch_activate_delete_flow(client, db_factory):
    await login(client, db_factory)
    created = (await client.post("/api/v1/search-plans", json=plan_payload())).json()
    plan_id = created["id"]

    # PATCH 更新
    patched = await client.patch(
        f"/api/v1/search-plans/{plan_id}",
        json={"status": "paused", "target_monthly_salary": 35000},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "paused"

    # PATCH 时结合现有值校验 minimum ≤ target
    bad = await client.patch(
        f"/api/v1/search-plans/{plan_id}", json={"target_monthly_salary": 10000}
    )
    assert bad.status_code == 422

    # activate 幂等激活
    activated = await client.post(f"/api/v1/search-plans/{plan_id}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"

    # 删除
    deleted = await client.delete(f"/api/v1/search-plans/{plan_id}")
    assert deleted.status_code == 200
    assert (await client.get(f"/api/v1/search-plans/{plan_id}")).status_code == 404


# ---------------- 3 个 active 上限 ----------------


async def test_active_plan_limit_api_sequential(client, db_factory):
    await login(client, db_factory)
    for i in range(3):
        resp = await client.post(
            "/api/v1/search-plans", json=plan_payload(name=f"方案{i}")
        )
        assert resp.status_code == 201, resp.text
    # 第 4 个 active → 409 稳定错误码
    resp = await client.post("/api/v1/search-plans", json=plan_payload(name="方案4"))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SEARCH_PLAN_ACTIVE_LIMIT"
    # paused 不受限
    resp = await client.post(
        "/api/v1/search-plans", json=plan_payload(name="暂停方案", status="paused")
    )
    assert resp.status_code == 201
    # 暂停方案 activate 也要被上限拦住
    resp = await client.post(f"/api/v1/search-plans/{resp.json()['id']}/activate")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SEARCH_PLAN_ACTIVE_LIMIT"


async def test_active_plan_limit_concurrent_db_level(client, db_factory):
    """DB 层并发安全：两个事务同时插入第 3、4 个 active，只允许一个成功。

    直接用两条 psycopg 连接模拟并发窗口（事务内插入不提交 → 另一事务
    被 advisory lock 阻塞 → 提交后另一事务计数看到新行而失败）。
    """
    user = await login(client, db_factory)
    for i in range(2):
        resp = await client.post(
            "/api/v1/search-plans", json=plan_payload(name=f"预置{i}")
        )
        assert resp.status_code == 201

    insert_sql = """
        INSERT INTO search_plans (
            id, user_id, name, role_family, status, priority, city_codes, work_modes,
            salary_currency, minimum_match_score, allow_outsourcing, created_at, updated_at
        ) VALUES (
            %s, %s, %s, 'backend_python', 'active', 0, '{110100}', '{onsite}',
            'CNY', 65, false, now(), now()
        )
    """
    results: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def worker(tag: str) -> None:
        try:
            with psycopg.connect(TEST_SYNC_DSN) as conn:
                with conn.transaction():
                    barrier.wait(timeout=10)
                    conn.execute(insert_sql, (str(uuid.uuid4()), str(user.id), f"并发{tag}"))
            results[tag] = "ok"
        except psycopg.errors.CheckViolation:
            results[tag] = "limited"
        except Exception as exc:  # pragma: no cover - 失败时便于诊断
            results[tag] = f"error:{type(exc).__name__}"

    threads = [threading.Thread(target=worker, args=(tag,)) for tag in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(results.values()) == ["limited", "ok"], results

    async with db_factory() as db:
        count = len(
            (
                await db.execute(
                    select(SearchPlan).where(
                        SearchPlan.user_id == user.id, SearchPlan.status == "active"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert count == 3, "并发窗口下 active 方案数不得超过 3"


# ---------------- 跨用户越权 ----------------


async def test_cross_user_access_returns_404(client, make_client, db_factory):
    await login(client, db_factory)
    created = (await client.post("/api/v1/search-plans", json=plan_payload())).json()
    plan_id = created["id"]

    other = make_client()
    async with other:
        await login(other, db_factory)
        assert (await other.get(f"/api/v1/search-plans/{plan_id}")).status_code == 404
        assert (
            await other.patch(f"/api/v1/search-plans/{plan_id}", json={"name": "劫持"})
        ).status_code == 404
        assert (await other.delete(f"/api/v1/search-plans/{plan_id}")).status_code == 404
        assert (
            await other.get(f"/api/v1/search-plans/{plan_id}/company-preferences")
        ).status_code == 404


# ---------------- 公司偏好与学习偏好 ----------------


async def test_company_preferences_put_get(client, db_factory):
    await login(client, db_factory)
    plan_id = (await client.post("/api/v1/search-plans", json=plan_payload())).json()["id"]

    put = await client.put(
        f"/api/v1/search-plans/{plan_id}/company-preferences",
        json={
            "items": [
                {"company_name": "星岚科技", "preference": "priority"},
                {"company_name": "云图智能", "preference": "follow"},
                {"company_name": "某外包公司", "preference": "block"},
            ]
        },
    )
    assert put.status_code == 200, put.text
    got = await client.get(f"/api/v1/search-plans/{plan_id}/company-preferences")
    assert got.status_code == 200
    items = {i["company_name"]: i["preference"] for i in got.json()["items"]}
    assert items == {"星岚科技": "priority", "云图智能": "follow", "某外包公司": "block"}

    # 整体替换语义
    put2 = await client.put(
        f"/api/v1/search-plans/{plan_id}/company-preferences",
        json={"items": [{"company_name": "星岚科技", "preference": "block"}]},
    )
    assert put2.status_code == 200
    items2 = (await client.get(f"/api/v1/search-plans/{plan_id}/company-preferences")).json()
    assert [(i["company_name"], i["preference"]) for i in items2["items"]] == [
        ("星岚科技", "block")
    ]

    # 非法 preference 拒绝
    bad = await client.put(
        f"/api/v1/search-plans/{plan_id}/company-preferences",
        json={"items": [{"company_name": "X", "preference": "love"}]},
    )
    assert bad.status_code == 422


async def test_reset_learned_preferences_versioned(client, db_factory):
    user = await login(client, db_factory)
    r1 = await client.post("/api/v1/preferences/reset-learned")
    assert r1.status_code == 200
    assert r1.json()["new_version"] == 1
    r2 = await client.post("/api/v1/preferences/reset-learned")
    assert r2.status_code == 200
    assert r2.json()["new_version"] == 2

    async with db_factory() as db:
        rows = (
            (
                await db.execute(
                    select(LearnedPreference).where(LearnedPreference.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
    # 版本化：历史保留为 reset，只有最新一条 active
    assert len(rows) == 2
    assert sorted(r.version for r in rows) == [1, 2]
    active = [r for r in rows if r.status == "active"]
    assert len(active) == 1 and active[0].version == 2


async def test_unauthenticated_rejected(client):
    assert (await client.get("/api/v1/search-plans")).status_code == 401
    assert (await client.post("/api/v1/search-plans", json=plan_payload())).status_code == 401
