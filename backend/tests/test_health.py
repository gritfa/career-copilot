"""健康端点语义测试（无真实 DB/Redis）。"""

EXPECTED_CAPABILITIES = {
    "job_source",
    "deepseek_generation",
    "qwen_fallback",
    "aliyun_embedding",
    "resume_parse_pdf",
    "resume_parse_docx",
    "docx_export",
    "pdf_export",
    "email_magic_link",
}


async def test_live_returns_200_without_dependencies(client):
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}
    assert resp.headers["X-Request-ID"].startswith("req_")


async def test_capabilities_all_not_verified(client):
    resp = await client.get("/health/capabilities")
    assert resp.status_code == 200
    caps = resp.json()["capabilities"]
    assert set(caps) == EXPECTED_CAPABILITIES
    # 阶段 1 硬约束：任何能力都不允许标为 ready
    for name, status in caps.items():
        assert status == "not_verified", f"capability {name} must be not_verified, got {status}"


async def test_ready_returns_503_when_dependencies_down(client):
    resp = await client.get("/health/ready")
    assert resp.status_code == 503

    body = resp.json()
    assert body["status"] == "not_ready"
    checks = body["checks"]
    assert set(checks) == {"database", "redis", "migrations"}
    for name in ("database", "redis", "migrations"):
        assert checks[name]["status"] == "failed", f"{name} should be failed"
        assert checks[name]["reason"], f"{name} should carry a structured reason"

    # 失败原因不得泄漏 DSN / 密码
    assert "secret-db-pass" not in resp.text
    assert "tester" not in resp.text
