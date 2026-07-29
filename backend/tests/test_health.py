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

# 阶段 3：只有本机真实解析集成测试通过的两项才允许 ready
READY_CAPABILITIES = {"resume_parse_pdf", "resume_parse_docx"}


async def test_live_returns_200_without_dependencies(client):
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}
    assert resp.headers["X-Request-ID"].startswith("req_")


async def test_capabilities_only_verified_ready(client):
    resp = await client.get("/health/capabilities")
    assert resp.status_code == 200
    caps = resp.json()["capabilities"]
    assert set(caps) == EXPECTED_CAPABILITIES
    # 硬约束：未经真实验证的能力（deepseek/qwen/embedding/来源/导出/邮件）
    # 绝不允许标 ready；已验证的解析能力必须如实标 ready。
    for name, status in caps.items():
        if name in READY_CAPABILITIES:
            assert status == "ready", f"capability {name} verified in phase 3, got {status}"
        else:
            assert status == "not_verified", (
                f"capability {name} must be not_verified, got {status}"
            )


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
