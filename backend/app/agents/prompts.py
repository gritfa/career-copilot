"""标准分析：输入文档构造与提示词（阶段 6，单模型标准分析）。

- 输入只含：已确认事实（过滤受保护属性与联系方式）、求职方案基础信息、
  岗位原文/标准化字段、规则分与硬条件明细、风险信号。
- 性别/年龄/照片/婚育/民族/宗教/籍贯绝不进入 prompt（docs/07 第 6 节）；
  解析层已丢弃这些事实类型，这里再做一层防御性过滤。
- 输入文档做 canonical JSON 序列化 → sha256 指纹（agent_runs.input_fingerprint），
  同输入可复现、可去重。
"""

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from app.db.models import MatchComponent, ProfileFact
from app.integrations.llm_gateway import LLMRequest
from app.resumes.parser import PROTECTED_FACT_TYPES

PROMPT_VERSION = "std_prompt_v1"
INPUT_VERSION = "std_input_v1"

# 事实 value_json 中即使意外出现联系方式类键也不进 prompt（防御性）
_DROPPED_VALUE_KEYS = frozenset({"phone", "mobile", "email", "wechat", "qq", "address"})

INPUT_START = "<ANALYSIS_INPUT>"
INPUT_END = "</ANALYSIS_INPUT>"

_SYSTEM_PROMPT = (
    "你是求职匹配分析助手。你将收到一个 JSON 输入文档，"
    "包含用户已确认的简历事实（含 fact_id）、求职方案、岗位原文与确定性规则评分。\n"
    "硬性规则：\n"
    "1. 只能使用输入文档中的事实与岗位原文，禁止使用任何外部常识补足用户的技能或经历；\n"
    "2. 每条结论必须绑定证据：profile_fact_ids 只能取输入中的 fact_id，"
    "job_span 必须逐字取自岗位原文或输入中的证据片段；\n"
    "3. 找不到证据时必须显式设置 uncertainty=\"insufficient_evidence\"，不得编造；\n"
    "4. 不得修改硬条件结论，不得把风险信号定性为公司违法/诈骗；\n"
    "5. 简历建议只能调整表达与突出重点，禁止发明技能、数字、经历；\n"
    "6. 只输出符合目标 Schema 的 JSON，不输出任何解释、推理过程或其他内容。"
)


def _filtered_fact_value(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k not in _DROPPED_VALUE_KEYS}


def build_analysis_input(
    *,
    facts: Sequence[ProfileFact],
    plan,
    posting,
    recommendation,
    components: Sequence[MatchComponent],
) -> dict[str, Any]:
    """构造分析输入文档（确定性：字段排序稳定）。

    facts 为已确认 ProfileFact；受保护属性类型（性别/年龄/婚育/民族/籍贯等）
    在此处再次过滤，绝不进入 prompt。
    """
    fact_items = [
        {
            "fact_id": str(f.id),
            "fact_type": f.fact_type,
            "value": _filtered_fact_value(dict(f.value_json or {})),
        }
        for f in sorted(facts, key=lambda f: str(f.id))
        if f.fact_type not in PROTECTED_FACT_TYPES
    ]
    component_items = [
        {
            "component": c.component,
            "score": c.score,
            "weight": c.weight,
            "gap_level": c.gap_level,
            "uncertainty": c.uncertainty,
            "evidence_refs": list(c.evidence_refs_json or []),
        }
        for c in sorted(components, key=lambda c: c.component)
    ]
    return {
        "input_version": INPUT_VERSION,
        "plan": {
            "role_family": plan.role_family,
            "city_codes": list(plan.city_codes or []),
            "work_modes": list(plan.work_modes or []),
            "target_monthly_salary": plan.target_monthly_salary,
            "minimum_monthly_salary": plan.minimum_monthly_salary,
        },
        "facts": fact_items,
        "job": {
            "title": posting.title_raw or "",
            "description": posting.description_text or "",
            "salary_raw": posting.salary_raw or "",
            "city_code": posting.city_code,
            "experience_min": posting.experience_min,
            "experience_max": posting.experience_max,
            "education_level": posting.education_level,
            "education_requirement_type": posting.education_requirement_type,
        },
        "rule_score": {
            "total": recommendation.score_total,
            "grade": recommendation.grade,
            "scoring_version": recommendation.scoring_version,
            "components": component_items,
        },
        "hard_conditions": list(recommendation.hard_conditions_json or []),
        "risk_signals": list(posting.outsourcing_signals or []),
    }


def input_fingerprint(input_doc: dict[str, Any]) -> str:
    """canonical JSON → sha256（用于 agent_runs.input_fingerprint）。"""
    canonical = json.dumps(input_doc, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_analysis_request(input_doc: dict[str, Any]) -> LLMRequest:
    """输入文档 → Gateway 请求（提示词正文绝不入日志）。"""
    payload = json.dumps(input_doc, ensure_ascii=False, sort_keys=True)
    user = (
        "请基于以下输入文档产出标准分析报告 JSON"
        "（字段：schema_version/overall_summary/strengths/gaps/risks/resume_suggestions）。\n"
        f"{INPUT_START}\n{payload}\n{INPUT_END}"
    )
    return LLMRequest(system=_SYSTEM_PROMPT, user=user, schema_name="std_analysis_v1")


def extract_input_doc(user_message: str) -> dict[str, Any] | None:
    """从请求正文提取输入文档（合成 Adapter 使用；解析失败返回 None）。"""
    start = user_message.find(INPUT_START)
    end = user_message.rfind(INPUT_END)
    if start < 0 or end < 0 or end <= start:
        return None
    block = user_message[start + len(INPUT_START) : end].strip()
    try:
        parsed = json.loads(block)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
