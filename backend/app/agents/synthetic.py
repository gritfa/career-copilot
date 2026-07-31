"""确定性合成分析 Adapter（无真实模型 key 时的 Gateway 实现）。

性质（有单元测试保障）：
- 确定性：同一请求任何时刻产出完全相同的 JSON（同输入同输出）；
- 只搬运证据：结论全部派生自输入文档中的规则证据（fact_id + 岗位 span）
  与风险信号，绝不引入输入之外的"常识"，无证据处显式 insufficient_evidence；
- 零网络、零费用；对应能力 deepseek_generation / standard_analysis
  保持 not_verified（00-master：不虚标未验证能力）。

真实 DEEPSEEK_API_KEY 一填，get_llm_adapter() 即切换到 DeepSeekAdapter，
本实现不再被选择。
"""

import json
from typing import Any

from app.agents.prompts import extract_input_doc
from app.agents.schemas import (
    INSUFFICIENT_EVIDENCE,
    AnalysisClaim,
    AnalysisGap,
    AnalysisRisk,
    ClaimStrength,
    GapSeverity,
    ResumeSuggestion,
    StandardAnalysisReport,
)
from app.integrations.llm_gateway import LLMRawResponse, LLMRequest

_MAX_STRENGTHS = 10
_MAX_SUGGESTIONS = 8

_SEVERITY_BY_GAP_LEVEL: dict[str, GapSeverity] = {"minor": "minor", "major": "major"}


def _coerce_strength(value: str) -> ClaimStrength:
    """输入文档里的自由字符串 → 词表内取值；词表外一律回落 moderate。"""
    if value == "strong":
        return "strong"
    if value == "weak":
        return "weak"
    return "moderate"

_COMPONENT_LABELS = {
    "core_skills": "核心技能",
    "experience": "工作经验",
    "project_evidence": "项目证据",
    "role_semantic": "岗位方向语义",
    "industry": "行业背景",
    "preference": "用户偏好",
}


def _estimate_tokens(text: str) -> int:
    # 粗略估算（约 4 字符/token），仅用于费用账本；合成实现零费用
    return max(1, len(text) // 4)


def _build_report(input_doc: dict[str, Any]) -> StandardAnalysisReport:
    rule_score = input_doc.get("rule_score", {}) or {}
    components = rule_score.get("components", []) or []

    strengths: list[AnalysisClaim] = []
    gaps: list[AnalysisGap] = []
    suggestions: list[ResumeSuggestion] = []
    seen_claims: set[str] = set()

    for comp in components:
        comp_name = str(comp.get("component", ""))
        label = _COMPONENT_LABELS.get(comp_name, comp_name)
        gap_level = str(comp.get("gap_level", "unknown"))
        uncertainty = comp.get("uncertainty")
        refs = comp.get("evidence_refs", []) or []

        if uncertainty == INSUFFICIENT_EVIDENCE and not refs:
            gaps.append(
                AnalysisGap(
                    description=f"{label}分项在事实库中证据不足，无法给出结论",
                    job_span="",
                    severity="unknown",
                    uncertainty=INSUFFICIENT_EVIDENCE,
                )
            )
            continue

        for ref in refs:
            claim = str(ref.get("claim", "")).strip()
            fact_ids = sorted(str(fid) for fid in ref.get("profile_fact_ids", []) or [])
            span = str((ref.get("job_evidence") or {}).get("span", ""))
            strength = str(ref.get("strength", "moderate"))
            missing_terms = ref.get("missing_terms")

            if missing_terms:
                terms = "、".join(str(t) for t in missing_terms)
                gaps.append(
                    AnalysisGap(
                        description=f"岗位要求但事实库无证据的技能：{terms}",
                        job_span=span,
                        severity=_SEVERITY_BY_GAP_LEVEL.get(gap_level, "major"),
                        uncertainty=None if span else INSUFFICIENT_EVIDENCE,
                    )
                )
                for term in missing_terms:
                    suggestions.append(
                        ResumeSuggestion(
                            suggestion=(
                                f"岗位要求「{term}」但事实库中无对应证据；"
                                "请勿在简历中虚构该技能，可先补充学习，"
                                "或在确认真实掌握后把它加入事实库"
                            ),
                            based_on_fact_ids=[],
                            uncertainty=INSUFFICIENT_EVIDENCE,
                        )
                    )
                continue

            if not claim or not fact_ids or claim in seen_claims:
                continue
            seen_claims.add(claim)
            strengths.append(
                AnalysisClaim(
                    claim=claim,
                    profile_fact_ids=fact_ids,
                    job_span=span,
                    strength=_coerce_strength(strength),
                    uncertainty=(
                        str(ref["uncertainty"]) if ref.get("uncertainty") else None
                    ),
                )
            )
            suggestions.append(
                ResumeSuggestion(
                    suggestion=f"在简历中优先突出「{claim}」对应的经历与产出（引用已确认事实）",
                    based_on_fact_ids=fact_ids,
                    uncertainty=None,
                )
            )

    risks = [
        AnalysisRisk(
            code=str(signal.get("code", "UNKNOWN")),
            evidence=str(signal.get("evidence", "")),
            confidence=(
                float(signal["confidence"])
                if isinstance(signal.get("confidence"), int | float)
                else None
            ),
        )
        for signal in (input_doc.get("risk_signals", []) or [])
    ]

    strengths = strengths[:_MAX_STRENGTHS]
    suggestions = suggestions[:_MAX_SUGGESTIONS]
    total = rule_score.get("total", "?")
    grade = rule_score.get("grade", "?")
    summary = (
        f"规则综合评分 {total}/100（等级 {grade}）。"
        f"识别匹配亮点 {len(strengths)} 项、关键缺口 {len(gaps)} 项、"
        f"风险信号 {len(risks)} 项。"
        "本报告由确定性合成分析生成（真实模型未验证），"
        "全部结论仅基于已确认事实与岗位原文，未使用任何外部推断。"
    )
    return StandardAnalysisReport(
        overall_summary=summary,
        strengths=strengths,
        gaps=gaps,
        risks=risks,
        resume_suggestions=suggestions,
    )


class SyntheticAnalysisAdapter:
    """合成 LLM 实现：从请求中的输入文档确定性派生报告。"""

    provider = "synthetic"
    model_id = "synthetic-analysis@1"

    @property
    def configured(self) -> bool:
        return True

    def complete(self, request: LLMRequest) -> LLMRawResponse:
        input_doc = extract_input_doc(request.user)
        if input_doc is None:
            # 与真实模型输出坏 JSON 同构：交给 Gateway 的 Schema 校验/修复路径
            content = json.dumps({"error": "no analysis input found"}, ensure_ascii=False)
        else:
            report = _build_report(input_doc)
            content = report.model_dump_json()
        return LLMRawResponse(
            content=content,
            tokens_in=_estimate_tokens(request.system) + _estimate_tokens(request.user),
            tokens_out=_estimate_tokens(content),
            provider_request_id=None,
        )
