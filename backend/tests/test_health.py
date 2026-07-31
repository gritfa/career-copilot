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
# PG(pgvector)/Redis 容器集成测试验证 → ready；
# 阶段 11 P0（2026-07-30，docs/16）：standard_analysis 管线与 DOCX/PDF 导出
# 经真实 DeepSeek 三链路实跑验证 → ready（产出真实性另由 deepseek_generation
# 三态反映，不在此虚标）。

READY_CAPABILITIES = {
    "resume_parse_pdf",
    "resume_parse_docx",
    "matching_basic",
    "standard_analysis",
    "docx_export",
    "pdf_export",
}

# 阶段 4：BOSS 无允许的自动访问方式，能力如实标 import_only（绝不显示采集正常）
IMPORT_ONLY_CAPABILITIES = {"job_source:boss"}

# 阶段 11 P0：模型能力改为三态对象（configured / runtime / last_verified），
# 不再是单一字符串；当前可用性只看 configured + runtime，历史证据独立留档。
MODEL_CAPABILITIES = {"deepseek_generation", "qwen_fallback", "aliyun_embedding"}

# 三态对象契约（scripts/acceptance.py step_health 用同一契约做验收 Gate；
# PR#4 review 第 1 条：CI 用本契约测试拦住结构漂移，验收脚本不再是唯一防线）
MODEL_REQUIRED_FIELDS = {
    "status", "configured", "runtime", "runtime_checked_at", "active_adapter", "last_verified",
}
MODEL_STATUS_DOMAIN = {"not_configured", "configured", "available", "unavailable"}
MODEL_RUNTIME_DOMAIN = {"not_probed", "available", "unavailable"}


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


def _assert_three_state_contract(entry: dict, name: str) -> None:
    """三态对象结构契约：字段齐全、取值域合法、status 严格由 configured/runtime 推导。"""
    assert isinstance(entry, dict), f"能力 {name} 应为三态对象，实际: {entry!r}"
    missing = MODEL_REQUIRED_FIELDS - set(entry)
    assert not missing, f"能力 {name} 缺字段: {sorted(missing)}"
    assert isinstance(entry["configured"], bool), name
    assert entry["status"] in MODEL_STATUS_DOMAIN, f"{name}: {entry['status']!r}"
    assert entry["runtime"] in MODEL_RUNTIME_DOMAIN, f"{name}: {entry['runtime']!r}"
    if entry["runtime_checked_at"] is not None:
        assert isinstance(entry["runtime_checked_at"], str), name
    if entry["last_verified"] is not None:
        assert isinstance(entry["last_verified"], dict), name
        assert {"verified_at", "model_id", "evidence"} <= set(entry["last_verified"]), name
    # 证据不升级当前态：status 只能由 configured + runtime 推导，
    # last_verified 有无历史证据都不许改变它
    expected = (
        "not_configured" if not entry["configured"]
        else {"available": "available", "unavailable": "unavailable"}.get(
            entry["runtime"], "configured")
    )
    assert entry["status"] == expected, (
        f"能力 {name} status={entry['status']} 与 configured/runtime 推导不符（应为 {expected}）"
    )
    if not entry["configured"]:
        assert entry["runtime"] == "not_probed", f"能力 {name} 无 key 却报探测结果（虚标）"


async def test_capabilities_contract_three_state_structure(client):
    """契约测试（PR#4 review 第 1 条）：钉死 /health/capabilities 三态返回结构。

    验收脚本 step_health 依赖此结构；此前结构改动只有验收脚本能发现而 CI
    不跑验收脚本 → 负责人 Windows 实测才暴露。此测试让 CI 直接拦住结构漂移。
    """
    caps = (await client.get("/health/capabilities")).json()["capabilities"]
    for name in MODEL_CAPABILITIES:
        _assert_three_state_contract(caps[name], name)
    # 非模型能力保持字符串状态（两类形态不得混淆）
    for name in EXPECTED_CAPABILITIES - MODEL_CAPABILITIES:
        assert isinstance(caps[name], str), f"非模型能力 {name} 应为字符串状态"


async def test_capabilities_contract_holds_with_key_probed(client, monkeypatch):
    """有 key + 探测通过的形态同样满足契约（验收有 key 环境 available 也 PASS）。"""
    from app.core.config import get_settings

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-contract-test")
    get_settings.cache_clear()

    async def _probe_ok(self):
        return True

    monkeypatch.setattr(health_module.DeepSeekAdapter, "probe_runtime", _probe_ok)
    try:
        caps = (await client.get("/health/capabilities")).json()["capabilities"]
        for name in MODEL_CAPABILITIES:
            _assert_three_state_contract(caps[name], name)
        assert caps["deepseek_generation"]["status"] == "available"
    finally:
        get_settings.cache_clear()


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
    # 2026-07-30 实跑证据已留档（docs/16），但那只是历史证据——
    # 上面已断言当前 status 仍是 not_configured，绝不因证据虚标可用
    deepseek_evidence = caps["deepseek_generation"]["last_verified"]
    assert deepseek_evidence is not None
    assert deepseek_evidence["evidence"] == "docs/16-real-model-verification.md"
    # 从未验证的能力证据必须为 None（不许占位）
    assert caps["qwen_fallback"]["last_verified"] is None
    assert caps["aliyun_embedding"]["last_verified"] is None


async def test_model_capability_probed_available(client, monkeypatch):
    """有 key + 探测通过 → status=available；探测结果带时间戳并被 TTL 缓存。"""
    from app.core.config import get_settings

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-probe-test")
    get_settings.cache_clear()
    probe_calls = {"n": 0}

    async def _probe(self):
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

    async def _probe_fail(self):
        return False

    monkeypatch.setattr(health_module.DeepSeekAdapter, "probe_runtime", _probe_fail)
    try:
        entry = (await client.get("/health/capabilities")).json()["capabilities"][
            "deepseek_generation"
        ]
        assert entry["runtime"] == "unavailable"
        assert entry["status"] == "unavailable"
    finally:
        get_settings.cache_clear()


async def test_concurrent_cold_start_probes_only_once(client, monkeypatch):
    """PR#4 review 第 3 条：冷启动并发请求经 asyncio.Lock 串行化，只外呼一次。"""
    import asyncio

    from app.core.config import get_settings

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-probe-test")
    get_settings.cache_clear()
    probe_calls = {"n": 0}

    async def _slow_probe(self):
        probe_calls["n"] += 1
        await asyncio.sleep(0.05)  # 拉长探测窗口，保证两请求真正并发进入
        return True

    monkeypatch.setattr(health_module.DeepSeekAdapter, "probe_runtime", _slow_probe)
    try:
        r1, r2 = await asyncio.gather(
            client.get("/health/capabilities"), client.get("/health/capabilities")
        )
        for resp in (r1, r2):
            entry = resp.json()["capabilities"]["deepseek_generation"]
            assert entry["runtime"] == "available"
            assert entry["status"] == "available"
        assert probe_calls["n"] == 1, "冷启动并发必须只探测一次（锁 + 双检缓存）"
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
