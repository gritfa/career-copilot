"""测试夹具：把依赖指向不可达端口，健康检查不需要真实 DB/Redis。"""

import os

# 必须在导入 app 之前设置：指向本机必然拒绝连接的端口（port 1）
os.environ["ENV"] = "test"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://tester:secret-db-pass@127.0.0.1:1/cc_test"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
os.environ["HEALTH_CHECK_TIMEOUT_SECONDS"] = "1.0"

# 供应商密钥隔离（阶段 10 测试质量项；阶段 11 P0 修复）：测试绝不依赖/泄用
# 机器环境里的真实 key。注意必须**置空**而非 pop：pydantic-settings 还会读
# backend/.env 文件，pop 掉环境变量后 .env 里的真实 key 仍会被加载（P0 实测暴露
# 的泄漏路径）；空字符串环境变量优先于 .env 文件值，能真正隔离。
_PROVIDER_KEY_ENVS = ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY")
for _key in _PROVIDER_KEY_ENVS:
    os.environ[_key] = ""

import pytest  # noqa: E402
import structlog  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

# 测试中禁用 logger 缓存：否则先跑的测试会用原始 processors 缓存 logger，
# 之后 structlog.testing.capture_logs() 拦不到日志（单跑过、全量挂的元凶）。
structlog.configure(cache_logger_on_first_use=False)


@pytest.fixture(autouse=True)
def _isolate_provider_keys(monkeypatch):
    """每个测试再兜底一次：任何测试期间设置/残留的供应商 key 不跨测试泄漏。

    同样必须置空而非删除——删除会让 backend/.env 里的真实 key 重新可见。
    """
    for key in _PROVIDER_KEY_ENVS:
        monkeypatch.setenv(key, "")


@pytest.fixture
def app():
    """独立的应用实例。"""
    from app.main import create_app

    return create_app()


@pytest.fixture
async def client(app):
    """httpx ASGI 客户端，不起真实服务器。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
