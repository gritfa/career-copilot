"""确定性合成定制简历 Adapter（无真实模型 key 时的 Gateway 实现）。

性质（与 SyntheticAnalysisAdapter 同构，有测试保障）：
- 确定性：同一请求任何时刻产出完全相同的 JSON；
- 只搬运事实：条目文本逐字来自已确认事实的值，定制仅做"筛选 + 排序"
  （措辞调整留给真实模型路径），每处排序决策写入 changes 并绑定
  fact_ids 与岗位原文证据；
- 零网络、零费用；产出必须如实标注 not_verified（00-master：不虚标能力）。

真实 DEEPSEEK_API_KEY 一填，get_tailor_adapter() 即切换到 DeepSeekAdapter。
"""

import json
from typing import Any

from app.integrations.llm_gateway import LLMRawResponse, LLMRequest
from app.tailoring.prompts import extract_tailor_input
from app.tailoring.schemas import (
    ResumeChange,
    ResumeContent,
    ResumeItem,
    ResumeSection,
    TailoredResumeDraft,
)

_SECTION_ORDER = ("skills", "work_experience", "projects", "education")
_SECTION_TITLES = {
    "skills": "专业技能",
    "work_experience": "工作经历",
    "projects": "项目经历",
    "education": "教育背景",
}
_FACT_TYPE_TO_SECTION = {
    "skill": "skills",
    "work_experience": "work_experience",
    "project": "projects",
    "education": "education",
}


def _estimate_tokens(text: str) -> int:
    # 粗略估算（约 4 字符/token），仅用于费用账本；合成实现零费用
    return max(1, len(text) // 4)


def _item_text(fact_type: str, value: dict[str, Any]) -> str:
    """事实值 → 条目文本：只做拼接，字段值逐字保留（不改数字、不加修饰）。"""
    if fact_type == "skill":
        name = str(value.get("name", "")).strip()
        detail = str(value.get("detail", "")).strip()
        return f"{name}：{detail}" if name and detail else (name or detail)
    if fact_type == "work_experience":
        company = str(value.get("company", "")).strip()
        start = str(value.get("start_raw", "")).strip()
        end = str(value.get("end_raw", "")).strip()
        period = f"（{start}—{end}）" if start or end else ""
        return f"{company}{period}"
    if fact_type == "project":
        name = str(value.get("name", "")).strip()
        detail = str(value.get("detail", "") or value.get("description", "")).strip()
        return f"{name}：{detail}" if name and detail else (name or detail)
    if fact_type == "education":
        school = str(value.get("school", "")).strip()
        degree = str(value.get("degree", "")).strip()
        return " ".join(part for part in (school, degree) if part)
    # 未知类型：把值里的字符串字段按键序拼接（仍然逐字来自事实）
    parts = [str(v).strip() for _, v in sorted(value.items()) if isinstance(v, str)]
    return " ".join(p for p in parts if p)


def _matched_evidence(input_doc: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """fact_id → (claim, job_span)：规则评分证据里被岗位命中的事实。"""
    matched: dict[str, tuple[str, str]] = {}
    components = (input_doc.get("rule_score", {}) or {}).get("components", []) or []
    for comp in components:
        for ref in comp.get("evidence_refs", []) or []:
            claim = str(ref.get("claim", "")).strip()
            span = str((ref.get("job_evidence") or {}).get("span", ""))
            for fid in ref.get("profile_fact_ids", []) or []:
                matched.setdefault(str(fid), (claim, span))
    return matched


def _build_draft(input_doc: dict[str, Any]) -> TailoredResumeDraft:
    facts = input_doc.get("facts", []) or []
    job = input_doc.get("job", {}) or {}
    matched = _matched_evidence(input_doc)

    by_section: dict[str, list[dict[str, Any]]] = {kind: [] for kind in _SECTION_ORDER}
    for fact in facts:
        section = _FACT_TYPE_TO_SECTION.get(str(fact.get("fact_type", "")))
        if section is not None:
            by_section[section].append(fact)

    sections: list[ResumeSection] = []
    changes: list[ResumeChange] = []
    for kind in _SECTION_ORDER:
        entries = by_section[kind]
        if not entries:
            continue  # 空章节不输出（模板层处理"无内容"呈现）
        # 排序：岗位命中的事实在前（保持稳定的 fact_id 次序），其余在后
        ranked = sorted(
            entries,
            key=lambda f: (0 if str(f.get("fact_id")) in matched else 1, str(f.get("fact_id"))),
        )
        items: list[ResumeItem] = []
        for fact in ranked:
            fid = str(fact.get("fact_id"))
            text = _item_text(
                str(fact.get("fact_type", "")), dict(fact.get("value") or {})
            )
            if not text:
                continue
            items.append(ResumeItem(text=text, fact_ids=[fid]))
            if fid in matched:
                claim, span = matched[fid]
                changes.append(
                    ResumeChange(
                        change_type="reordered",
                        section=kind,
                        before="",
                        after=text,
                        reason=f"优先展示与岗位要求匹配的已确认事实：{claim}",
                        fact_ids=[fid],
                        job_span=span,
                    )
                )
        if items:
            sections.append(
                ResumeSection(kind=kind, title=_SECTION_TITLES[kind], items=items)
            )

    content = ResumeContent(
        target_job_title=str(job.get("title", "")),
        target_company=str(job.get("company", "")),
        sections=sections,
    )
    return TailoredResumeDraft(content=content, changes=changes)


class SyntheticTailorAdapter:
    """合成 LLM 实现：从请求中的输入文档确定性派生定制简历草稿。"""

    provider = "synthetic"
    model_id = "synthetic-tailor@1"

    @property
    def configured(self) -> bool:
        return True

    def complete(self, request: LLMRequest) -> LLMRawResponse:
        input_doc = extract_tailor_input(request.user)
        if input_doc is None:
            # 与真实模型输出坏 JSON 同构：交给 Gateway 的 Schema 校验/修复路径
            content = json.dumps({"error": "no tailor input found"}, ensure_ascii=False)
        else:
            content = _build_draft(input_doc).model_dump_json()
        return LLMRawResponse(
            content=content,
            tokens_in=_estimate_tokens(request.system) + _estimate_tokens(request.user),
            tokens_out=_estimate_tokens(content),
            provider_request_id=None,
        )


def get_tailor_adapter():
    """选择实现：有 DEEPSEEK_API_KEY 用真实 DeepSeek；否则确定性合成实现。"""
    from app.integrations.llm_gateway import DeepSeekAdapter

    deepseek = DeepSeekAdapter()
    if deepseek.configured:
        return deepseek
    return SyntheticTailorAdapter()
