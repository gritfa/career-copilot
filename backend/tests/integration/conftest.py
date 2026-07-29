"""集成测试夹具：连接真实 PostgreSQL(55432) / Redis(56379) / Mailpit(1025/8025)。

- 不用 SQLite 冒充生产数据层（规格硬约束）。
- 为不污染开发库，session 级夹具在同一 PG 实例上重建 ``careercopilot_test``
  并用 Alembic 迁移到 head；Redis 使用 db 1。
- 根 conftest 把环境指向不可达端口（阶段 1 离线测试用）；本目录的
  ``real_env`` 夹具按测试临时切换到真实依赖并在结束后还原，两组测试互不影响。
"""

import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
import redis as redis_sync
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[2]

PG_HOST = os.getenv("TEST_PG_HOST", "localhost")
PG_PORT = int(os.getenv("TEST_PG_PORT", "55432"))  # 本机 5432 被占，见 DEVELOPMENT.md；CI 传 5432
PG_USER = "careercopilot"
PG_PASSWORD = "careercopilot_dev"
TEST_DB = "careercopilot_test"

TEST_DATABASE_URL = (
    f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{TEST_DB}"
)
TEST_SYNC_DSN = (
    f"host={PG_HOST} port={PG_PORT} user={PG_USER} password={PG_PASSWORD} dbname={TEST_DB}"
)
ADMIN_SYNC_DSN = (
    f"host={PG_HOST} port={PG_PORT} user={PG_USER} password={PG_PASSWORD} dbname=careercopilot"
)
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:56379/1")
MAILPIT_API = os.getenv("TEST_MAILPIT_API", "http://localhost:8025/api/v1")
SMTP_PORT = os.getenv("TEST_SMTP_PORT", "1025")

TABLES = (
    # 阶段 8：数据导出 / 注销硬删
    "data_exports",
    "account_purge_runs",
    # 阶段 7：定制简历版本 / 导出
    "resume_exports",
    "resume_versions",
    # 阶段 6：标准分析
    "agent_runs",
    # 阶段 5：匹配 / 推荐 / 向量 / 费用账本
    "user_feedback",
    "match_components",
    "recommendations",
    "job_vectors",
    "profile_vectors",
    "usage_ledger",
    "fact_evidence",
    "profile_facts",
    "fact_candidates",
    "resume_parses",
    "resumes",
    # 阶段 4：求职方案 / 岗位来源（job_snapshots 行级触发器不拦 TRUNCATE，仅限测试清理）
    "company_preferences",
    "learned_preferences",
    "search_plans",
    "job_postings",
    "canonical_jobs",
    "job_snapshots",
    "source_runs",
    "job_sources",
    "companies",
    "audit_events",
    "consents",
    "sessions",
    "auth_tokens",
    "invites",
    "users",
)


@pytest.fixture(scope="session")
def prepare_test_db() -> str:
    """重建 careercopilot_test 并迁移到 head（session 级，一次）。"""
    with psycopg.connect(ADMIN_SYNC_DSN, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB} OWNER {PG_USER}")

    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"
    return TEST_DATABASE_URL


@pytest.fixture
def real_env(prepare_test_db, monkeypatch, tmp_path):
    """把配置切到真实依赖；结束后由 monkeypatch 自动还原并清缓存。

    阶段 3：本地对象存储指向测试临时目录；Celery 走 eager
    （任务代码仍是真实 Celery 任务，经 dispatch_task 的 apply 路径同步执行）。
    """
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
    monkeypatch.setenv("SMTP_HOST", "localhost")
    monkeypatch.setenv("SMTP_PORT", SMTP_PORT)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean_state(request):
    """每个集成测试前清空业务表、Redis db1 和 Mailpit 收件箱。

    只对依赖 real_env 的测试生效（通过 fixture 声明检测），
    避免拖慢阶段 1 离线测试。
    """
    if "real_env" not in request.fixturenames:
        yield
        return
    request.getfixturevalue("real_env")
    with psycopg.connect(TEST_SYNC_DSN, autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} CASCADE")
    r = redis_sync.Redis.from_url(TEST_REDIS_URL)
    r.flushdb()
    r.close()
    httpx.delete(f"{MAILPIT_API}/messages", timeout=5)
    yield


@pytest.fixture
async def app(real_env):
    """连真实依赖的应用实例；结束时释放引擎/Redis。"""
    from app.core.redis import close_redis
    from app.db.session import dispose_engine
    from app.main import create_app

    application = create_app()
    yield application
    await dispose_engine(application)
    await close_redis(application)


@pytest.fixture
async def client(app):
    """https base_url：让 Secure cookie 在测试里可用。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def make_client(app):
    """为多用户场景创建独立 cookie jar 的客户端工厂。"""

    def _make() -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        )

    return _make


@pytest.fixture
def db_factory(app):
    from app.db.session import get_sessionmaker

    return get_sessionmaker(app)


# ---------- 数据工厂 ----------


async def create_invite(db_factory, code: str, max_uses: int = 1, **kwargs):
    from app.core.security import hash_invite_code
    from app.db.models import Invite

    async with db_factory() as db:
        invite = Invite(
            code_hash=hash_invite_code(code), max_uses=max_uses, used_count=0, **kwargs
        )
        db.add(invite)
        await db.commit()
        await db.refresh(invite)
        return invite


async def create_user(db_factory, email: str, role: str = "user", status: str = "active"):
    from app.core.security import normalize_email
    from app.db.models import User

    async with db_factory() as db:
        user = User(
            email_normalized=normalize_email(email),
            role=role,
            status=status,
            age_attested_at=datetime.now(UTC),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def create_session_for(db_factory, user) -> str:
    """直接建会话，返回 cookie 用的原始 token。"""
    from app.core.security import generate_token, hash_token
    from app.db.models import Session as DbSession

    raw = generate_token()
    async with db_factory() as db:
        session = DbSession(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        db.add(session)
        await db.commit()
    return raw


def session_cookie() -> str:
    return get_settings().session_cookie_name


def unique_email(prefix: str = "u") -> str:
    # 注意：email-validator 拒绝 .test/.example 等特殊用途域名，
    # 这里用普通形态的合成域名（pydantic 默认不做 DNS 校验）。
    return f"{prefix}-{uuid.uuid4().hex[:10]}@cc-integration.dev"


# ---------- Mailpit ----------


def mailpit_find_token(to_email: str) -> str:
    """从 Mailpit 找到发给 to_email 的最新邮件并抽取 magic link token。"""
    resp = httpx.get(f"{MAILPIT_API}/messages", timeout=5)
    resp.raise_for_status()
    for msg in resp.json()["messages"]:
        recipients = [addr["Address"].lower() for addr in msg["To"]]
        if to_email.lower() in recipients:
            detail = httpx.get(f"{MAILPIT_API}/message/{msg['ID']}", timeout=5)
            detail.raise_for_status()
            body = detail.json()["Text"]
            match = re.search(r"token=([A-Za-z0-9_-]+)", body)
            assert match, f"magic link token not found in email body: {body!r}"
            return match.group(1)
    raise AssertionError(f"no mailpit message for {to_email}")


async def request_magic_link(client, email: str, invite_code: str, age_attested: bool = True):
    return await client.post(
        "/api/v1/auth/magic-links",
        json={"email": email, "invite_code": invite_code, "age_attested": age_attested},
    )


async def signup_and_login(client, db_factory, email: str, invite_code: str | None = None):
    """完整注册/登录流：邀请码 → magic link → Mailpit 取 token → verify。

    返回 (verify_response_json, raw_session_cookie_value)。
    """
    if invite_code is None:
        invite_code = f"inv-{uuid.uuid4().hex[:8]}"
        await create_invite(db_factory, invite_code, max_uses=10)
    resp = await request_magic_link(client, email, invite_code)
    assert resp.status_code == 202, resp.text
    token = mailpit_find_token(email)
    verify = await client.post("/api/v1/auth/magic-links/verify", json={"token": token})
    assert verify.status_code == 200, verify.text
    raw_cookie = verify.cookies.get(session_cookie())
    assert raw_cookie
    return verify.json(), raw_cookie
