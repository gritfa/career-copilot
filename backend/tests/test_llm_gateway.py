"""Model Gateway 单元测试（离线，不连任何真实供应商）。

覆盖：allowlist、未配置/未授权门控、有限重试、Schema 校验 + 一次修复、
用量记账数字、日志无正文边界、合成 Adapter 确定性与证据约束。
"""

import json

import pytest
import structlog.testing
from pydantic import BaseModel

from app.agents.prompts import build_analysis_request, extract_input_doc
from app.agents.schemas import StandardAnalysisReport
from app.agents.synthetic import SyntheticAnalysisAdapter
from app.integrations.llm_gateway import (
    ConsentDecision,
    DeepSeekAdapter,
    LLMAuthorizationError,
    LLMError,
    LLMModelNotAllowedError,
    LLMNotConfiguredError,
    LLMRawResponse,
    LLMRequest,
    LLMSchemaError,
    ModelGateway,
    get_llm_adapter,
)


class _Out(BaseModel):
    answer: str


def _request(user: str = "分析这份输入") -> LLMRequest:
    return LLMRequest(system="系统提示", user=user, schema_name="test_out_v1")


class FakeAdapter:
    """可编排的假 Adapter：按脚本依次返回响应或抛错。"""

    provider = "deepseek"
    model_id = "deepseek-chat"

    def __init__(self, script, configured=True, provider=None, model_id=None):
        self.script = list(script)
        self.calls: list[LLMRequest] = []
        self._configured = configured
        if provider:
            self.provider = provider
        if model_id:
            self.model_id = model_id

    @property
    def configured(self):
        return self._configured

    def complete(self, request: LLMRequest) -> LLMRawResponse:
        self.calls.append(request)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return LLMRawResponse(content=step, tokens_in=100, tokens_out=50)


def _ok(content: str = '{"answer": "ok"}') -> str:
    return content


ALLOWED = ConsentDecision(allowed=True)


# ---------------- 门控：allowlist / 未配置 / 未授权 ----------------


def test_model_not_in_allowlist_rejected():
    adapter = FakeAdapter([_ok()], model_id="deepseek-chat-latest-untracked")
    gateway = ModelGateway(adapter)
    with pytest.raises(LLMModelNotAllowedError):
        gateway.complete_json(_request(), _Out, consent=ALLOWED)
    assert adapter.calls == []  # 不在 allowlist：绝不调用


def test_not_configured_rejected_without_call():
    adapter = FakeAdapter([_ok()], configured=False)
    gateway = ModelGateway(adapter)
    with pytest.raises(LLMNotConfiguredError):
        gateway.complete_json(_request(), _Out, consent=ALLOWED)
    assert adapter.calls == []


def test_real_provider_requires_consent():
    """deepseek/qwen 无有效授权 → 拒绝且不发起任何调用（docs/08 第 3 节）。"""
    adapter = FakeAdapter([_ok()])
    gateway = ModelGateway(adapter)
    with pytest.raises(LLMAuthorizationError):
        gateway.complete_json(_request(), _Out)  # 无授权决策
    with pytest.raises(LLMAuthorizationError):
        gateway.complete_json(
            _request(), _Out, consent=ConsentDecision(allowed=False, reason_code="NO_CONSENT")
        )
    assert adapter.calls == []


def test_synthetic_provider_needs_no_consent():
    """合成实现不出本机：无授权也可调用（不虚标为真实供应商）。"""
    adapter = FakeAdapter([_ok()], provider="synthetic", model_id="synthetic-analysis@1")
    result, usage = ModelGateway(adapter).complete_json(_request(), _Out)
    assert result.answer == "ok"
    assert usage.provider == "synthetic"
    assert usage.amount_estimated == 0.0  # 合成实现零费用


def test_deepseek_adapter_without_key_not_configured(monkeypatch):
    # 置空而非删除：删除会让 backend/.env 里的真实 key 经 env_file 泄入测试
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        adapter = DeepSeekAdapter()
        assert adapter.configured is False
        with pytest.raises(LLMNotConfiguredError):
            adapter.complete(_request())
        # 无 key 时选择合成实现（真实 key 一填即切换到 DeepSeekAdapter）
        assert get_llm_adapter().provider == "synthetic"
    finally:
        get_settings.cache_clear()


# ---------------- 有限重试 ----------------


def test_retry_then_success_and_usage_accounting(monkeypatch):
    monkeypatch.setattr("app.integrations.llm_gateway.time.sleep", lambda *_: None)
    adapter = FakeAdapter([LLMError("boom"), _ok()])
    result, usage = ModelGateway(adapter).complete_json(_request(), _Out, consent=ALLOWED)
    assert result.answer == "ok"
    assert len(adapter.calls) == 2  # 1 次失败 + 1 次成功
    # 记账只计成功返回的那次响应
    assert usage.tokens_in == 100 and usage.tokens_out == 50
    assert usage.attempts == 1 and usage.repaired is False
    # deepseek-chat 定价：2 元/1M 输入 + 8 元/1M 输出
    assert usage.amount_estimated == pytest.approx(
        100 / 1_000_000 * 2.0 + 50 / 1_000_000 * 8.0
    )


def test_retries_exhausted_raises(monkeypatch):
    monkeypatch.setattr("app.integrations.llm_gateway.time.sleep", lambda *_: None)
    adapter = FakeAdapter([LLMError("a"), LLMError("b"), LLMError("c"), LLMError("d")])
    with pytest.raises(LLMError):
        ModelGateway(adapter).complete_json(_request(), _Out, consent=ALLOWED)
    assert len(adapter.calls) == 3  # 1 + 2 次重试（LLM_MAX_RETRIES=2），不无限重放


# ---------------- Schema 校验：最多一次修复 ----------------


def test_schema_repair_once_then_success():
    adapter = FakeAdapter(["这不是 JSON", _ok()])
    result, usage = ModelGateway(adapter).complete_json(_request(), _Out, consent=ALLOWED)
    assert result.answer == "ok"
    assert len(adapter.calls) == 2
    assert usage.repaired is True
    assert usage.tokens_in == 200 and usage.tokens_out == 100  # 两次调用都记账
    # 修复请求带错误类别，但不复述模型的坏输出正文
    assert "invalid_json" in adapter.calls[1].user


def test_schema_fails_after_one_repair_no_fake_success():
    adapter = FakeAdapter(['{"wrong": 1}', '{"still": "wrong"}', _ok()])
    with pytest.raises(LLMSchemaError):
        ModelGateway(adapter).complete_json(_request(), _Out, consent=ALLOWED)
    assert len(adapter.calls) == 2  # 修复只允许一次，绝不第三次


# ---------------- 日志边界：正文绝不入日志 ----------------


def test_logs_contain_no_prompt_or_response_content(monkeypatch):
    monkeypatch.setattr("app.integrations.llm_gateway.time.sleep", lambda *_: None)
    canary_prompt = "PROMPTCANARY简历正文机密内容九三一"
    canary_output = "OUTPUTCANARY模型响应机密"
    adapter = FakeAdapter(
        [LLMError("flaky"), "非法JSON" + canary_output, json.dumps({"answer": canary_output})]
    )
    with structlog.testing.capture_logs() as logs:
        ModelGateway(adapter).complete_json(
            _request(user=canary_prompt), _Out, consent=ALLOWED
        )
    dumped = repr(logs)
    assert canary_prompt not in dumped
    assert canary_output not in dumped
    assert any(e.get("event") == "llm_completed" for e in logs)


# ---------------- 合成 Adapter：确定性与证据约束 ----------------


def _analysis_input() -> dict:
    return {
        "input_version": "std_input_v1",
        "plan": {"role_family": "backend_python", "city_codes": [], "work_modes": []},
        "facts": [
            {"fact_id": "fact-1", "fact_type": "skill", "value": {"name": "Python"}},
        ],
        "job": {"title": "Python 后端", "description": "负责 FastAPI 服务开发", "salary_raw": ""},
        "rule_score": {
            "total": 82,
            "grade": "high",
            "scoring_version": "score_v1",
            "components": [
                {
                    "component": "core_skills",
                    "score": 80,
                    "weight": 35,
                    "gap_level": "minor",
                    "uncertainty": None,
                    "evidence_refs": [
                        {
                            "claim": "具备 python 相关技能",
                            "profile_fact_ids": ["fact-1"],
                            "job_evidence": {"span": "Python 后端"},
                            "strength": "strong",
                            "uncertainty": None,
                        },
                        {
                            "claim": "岗位要求但事实库无证据的技能",
                            "profile_fact_ids": [],
                            "job_evidence": {"span": "负责 FastAPI 服务开发"},
                            "missing_terms": ["fastapi"],
                            "strength": "gap",
                            "uncertainty": None,
                        },
                    ],
                },
                {
                    "component": "project_evidence",
                    "score": 0,
                    "weight": 20,
                    "gap_level": "unknown",
                    "uncertainty": "insufficient_evidence",
                    "evidence_refs": [],
                },
            ],
        },
        "hard_conditions": [],
        "risk_signals": [
            {"code": "POSSIBLE_OUTSOURCING", "evidence": "需要驻场", "confidence": 0.7}
        ],
    }


def test_synthetic_adapter_deterministic_and_evidence_bound():
    request = build_analysis_request(_analysis_input())
    adapter = SyntheticAnalysisAdapter()
    first = adapter.complete(request)
    second = adapter.complete(request)
    assert first.content == second.content  # 同输入同输出

    report = StandardAnalysisReport.model_validate(json.loads(first.content))
    # 结论只引用输入中的事实 ID；缺证据处显式 insufficient_evidence
    assert report.strengths and report.strengths[0].profile_fact_ids == ["fact-1"]
    assert all(
        set(c.profile_fact_ids) <= {"fact-1"} for c in report.strengths
    )
    gap_texts = [g.description for g in report.gaps]
    assert any("fastapi" in t for t in gap_texts)
    assert any(g.uncertainty == "insufficient_evidence" for g in report.gaps)
    # 风险只转述输入信号
    assert [r.code for r in report.risks] == ["POSSIBLE_OUTSOURCING"]
    # 无证据的建议必须标注不确定；有证据的建议引用事实
    for s in report.resume_suggestions:
        assert s.based_on_fact_ids or s.uncertainty == "insufficient_evidence"
    # 合成来源如实声明
    assert "真实模型未验证" in report.overall_summary


def test_synthetic_adapter_via_gateway_validates_schema():
    request = build_analysis_request(_analysis_input())
    report, usage = ModelGateway(SyntheticAnalysisAdapter()).complete_json(
        request, StandardAnalysisReport
    )
    assert usage.provider == "synthetic" and usage.amount_estimated == 0.0
    assert usage.tokens_in > 0 and usage.tokens_out > 0  # 记账数字非零（估算）
    assert report.schema_version == "std_analysis_v1"


def test_extract_input_doc_roundtrip_and_garbage():
    doc = _analysis_input()
    assert extract_input_doc(build_analysis_request(doc).user) == doc
    assert extract_input_doc("没有输入标记") is None
