"""健康端点语义测试（无真实 DB/Redis）。"""

import pytest

from app.api import health as health_module

EXPECTED_CAPABILITIES = {
    "job_source:fixture_a",
    "job_source:fixture_b",
    "job_source:boss",
    "deepseek_generation",
    "qwen_fallback",
    "aliyun_embedding",
    "resume_parse_pdf",
    "resume_parse_docx",
    "docx_export",
    "pdf_export",
    "email_magic_link",
    "matching_basic",
    "standard_analysis",
}

# 阶段 3：本机真实解析集成测试通过的两项 ready；
# 阶段 5：matching_basic（硬条件+确定性向量召回+评分+反馈）经真实
# PG(pgvector)/Redis 容器集成测试验证 → ready。aliyun_embedding 无 key，
# 保持 not_verified。
READY_CAPABILITIES = {"resume_parse_pdf", "resume_parse_docx", "matching_basic"}

# 阶段 6（ADR-001 裁剪）：standard_analysis 管道经集成测试打通，但无真实
# DEEPSEEK_API_KEY（确定性合成 Adapter 产出，报告标 not_verified）→ 保持 not_verified。

# 阶段 4：BOSS 无允许的自动访问方式，能力如实标 import_only（绝不显示采集正常）
IMPORT_ONLY_CAPABILITIES = {"job_source:boss"}

# 阶段 11 P0：模型能力改为三态对象（configured / runtime / last_verified），
# 不再是单一字符串；当前可用性只看 configured + runtime，历史证据独立留档。
MODEL_CAPABILITIES = {"deepseek_generation", "qwen_fallback", "aliyun_embedding"}


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    health_module.reset_probe_cache()
    yield
    health_module.reset_probe_cache()


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
        if name in MODEL_CAPABILITIES:
            continue  # 模型能力三态对象另行断言
        if name in READY_CAPABILITIES:
            assert status == "ready", f"capability {name} verified in phase 3, got {status}"
        elif name in IMPORT_ONLY_CAPABILITIES:
            assert status == "import_only", (
                f"capability {name} must be import_only, got {status}"
            )
        else:
            assert status == "not_verified", (
                f"capability {name} must be not_verified, got {status}"
            )


async def test_model_capabilities_three_state_without_key(client):
    """无 key 环境：configured=False、runtime 不探测、status=not_configured。

    关键约束：即使 last_verified 留有历史实跑证据，当前状态也绝不因此变
    verified——三态相互独立（docs/15 P0 第 3 条）。
    """
    resp = await client.get("/health/capabilities")
    caps = resp.json()["capabilities"]
    for name in MODEL_CAPABILITIES:
        entry = caps[name]
        assert entry["configured"] is False, name
        assert entry["runtime"] == "not_probed", name
        assert entry["status"] == "not_configured", name
    # 无 key 时生成/嵌入由确定性合成实现兜底（如实声明）
    assert caps["deepseek_generation"]["active_adapter"] == "synthetic"
    assert caps["aliyun_embedding"]["active_adapter"] == "synthetic"
    assert caps["qwen_fallback"]["active_adapter"] is None


async def test_model_capability_probed_available(client, monkeypatch):
    """有 key + 探测通过 → status=available；探测结果带时间戳并被 TTL 缓存。"""
    from app.core.config import get_settings

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-probe-test")
    get_settings.cache_clear()
    probe_calls = {"n": 0}

    def _probe(self):
        probe_calls["n"] += 1
        return True

    monkeypatch.setattr(health_module.DeepSeekAdapter, "probe_runtime", _probe)
    try:
        caps = (await client.get("/health/capabilities")).json()["capabilities"]
        entry = caps["deepseek_generation"]
        assert entry["configured"] is True
        assert entry["runtime"] == "available"
        assert entry["status"] == "available"
        assert entry["runtime_checked_at"]
        assert entry["active_adapter"] == "deepseek"
        # TTL 缓存：第二次请求不重复探测
        await client.get("/health/capabilities")
        assert probe_calls["n"] == 1
    finally:
        get_settings.cache_clear()


async def test_model_capability_probed_unavailable(client, monkeypatch):
    """有 key 但探测失败 → 如实 unavailable，绝不因配置了 key 就显示可用。"""
    from app.core.config import get_settings

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-probe-test")
    get_settings.cache_clear()
    monkeypatch.setattr(
        health_module.DeepSeekAdapter, "probe_runtime", lambda self: False
    )
    try:
        entry = (await client.get("/health/capabilities")).json()["capabilities"][
            "deepseek_generation"
        ]
        assert entry["runtime"] == "unavailable"
        assert entry["status"] == "unavailable"
    finally:
        get_settings.cache_clear()


async def test_verified_evidence_never_upgrades_current_status(client, monkeypatch):
    """历史验证证据存在时，当前无 key 仍是 not_configured（防「跑过一次永久 verified」）。"""
    monkeypatch.setitem(
        health_module.MODEL_VERIFICATION_EVIDENCE,
        "deepseek_generation",
        {
            "verified_at": "2026-07-30",
            "model_id": "deepseek-chat",
            "evidence": "docs/16-real-model-verification.md",
        },
    )
    entry = (await client.get("/health/capabilities")).json()["capabilities"][
        "deepseek_generation"
    ]
    assert entry["last_verified"]["verified_at"] == "2026-07-30"
    assert entry["status"] == "not_configured"  # 证据不改变当前态
    assert entry["configured"] is False


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
