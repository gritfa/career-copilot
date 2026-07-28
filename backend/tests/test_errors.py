"""统一错误格式测试（docs/04-api.md 1.1 节）。"""

from httpx import ASGITransport, AsyncClient

from app.core.errors import AppError


def _assert_error_envelope(body: dict) -> dict:
    assert set(body) == {"error"}
    err = body["error"]
    assert isinstance(err["code"], str) and err["code"]
    assert isinstance(err["message"], str) and err["message"]
    assert err["request_id"].startswith("req_")
    return err


async def test_404_uses_unified_error_format(client):
    resp = await client.get("/no/such/route")
    assert resp.status_code == 404
    err = _assert_error_envelope(resp.json())
    assert err["code"] == "NOT_FOUND"
    # 响应头与错误体的 request_id 一致
    assert resp.headers["X-Request-ID"] == err["request_id"]


async def test_app_error_maps_code_status_and_details(app):
    @app.get("/boom")
    async def boom():
        raise AppError(
            code="CONSENT_REQUIRED",
            message="需要先授权 DeepSeek 处理完整简历",
            status_code=403,
            details={"provider": "deepseek", "allowed_fallback": "deidentified"},
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/boom")

    assert resp.status_code == 403
    err = _assert_error_envelope(resp.json())
    assert err["code"] == "CONSENT_REQUIRED"
    assert err["details"] == {"provider": "deepseek", "allowed_fallback": "deidentified"}


async def test_validation_error_does_not_echo_input(app):
    @app.get("/typed")
    async def typed(limit: int):
        return {"limit": limit}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/typed", params={"limit": "super-secret-input"})

    assert resp.status_code == 422
    err = _assert_error_envelope(resp.json())
    assert err["code"] == "VALIDATION_ERROR"
    # 不回显用户输入内容
    assert "super-secret-input" not in resp.text
