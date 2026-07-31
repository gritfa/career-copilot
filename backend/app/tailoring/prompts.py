"""定制简历生成：输入文档构造与提示词（阶段 7）。

- 输入只含：已确认事实（过滤受保护属性与联系方式）、岗位原文/标准化字段、
  规则评分证据（用于挑选/排序依据）；绝不含推荐之外的岗位、其他用户数据。
- 受保护属性（性别/年龄/照片/婚育/民族/宗教/籍贯）绝不进入 prompt，
  也绝不进入简历内容（validation 再拦一道）。
- 提示词正文（含事实/岗位内容）绝不入日志。
"""

import json
from collections.abc import Sequence
from typing import Any

from app.db.models import MatchComponent, ProfileFact
from app.integrations.llm_gateway import LLMRequest
from app.resumes.parser import PROTECTED_FACT_TYPES
from app.tailoring.schemas import TEMPLATE_ID, TailoredResumeDraft

# v2（阶段 11 P0）：prompt 内嵌输出 JSON Schema（与 agents/prompts 同一根因——
# v1 只列顶层字段名，真实模型无法得知嵌套对象结构）。
TAILOR_PROMPT_VERSION = "tailor_prompt_v2"
TAILOR_INPUT_VERSION = "tailor_input_v1"

# 目标输出 Schema（由 pydantic 模型确定性生成，随模型定义自动同步）
_OUTPUT_SCHEMA_JSON = json.dumps(
    TailoredResumeDraft.model_json_schema(), ensure_ascii=False, sort_keys=True
)

# 定制简历输出（多 section + 逐条 changes）明显长于分析报告，
# 默认 2048 token 有截断风险（截断 = 坏 JSON = 白付一次费用）
_TAILOR_MAX_OUTPUT_TOKENS = 4096

# 事实 value_json 中即使意外出现联系方式类键也不进 prompt（与 agents/prompts 一致）
_DROPPED_VALUE_KEYS = frozenset({"phone", "mobile", "email", "wechat", "qq", "address"})

INPUT_START = "<TAILOR_INPUT>"
INPUT_END = "</TAILOR_INPUT>"

_SYSTEM_PROMPT = (
    "你是简历定制助手。你将收到一个 JSON 输入文档，"
    "包含用户已确认的简历事实（含 fact_id）、目标岗位原文与确定性规则评分证据。\n"
    "硬性规则：\n"
    "1. 简历内容只能来自输入文档中的已确认事实；禁止虚构、夸大或用常识补足"
    "任何技能、经历、项目、任职时间或数字；\n"
    "2. 定制手段仅限：筛选（挑与岗位相关的事实）、排序（相关的放前面）、"
    "措辞调整（不改变事实含义）；\n"
    "3. 每条简历条目（item）必须绑定非空 fact_ids，且只能取输入中的 fact_id；\n"
    "4. 条目文本中的数字只能来自所引用事实的原值；缺失数字保持缺失，"
    "禁止估算或使用 XX% 之类占位符；\n"
    "5. 每处调整必须写入 changes：修改前、修改后、理由、fact_ids，"
    "job_span 必须逐字取自岗位原文；\n"
    "6. 只输出符合目标 Schema 的 JSON（schema_version/content/changes），"
    "不输出任何解释或其他内容。"
)


def _filtered_fact_value(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k not in _DROPPED_VALUE_KEYS}


def build_tailor_input(
    *,
    facts: Sequence[ProfileFact],
    posting,
    job_title: str,
    company_name: str,
    recommendation,
    components: Sequence[MatchComponent],
) -> dict[str, Any]:
    """构造定制输入文档（确定性：字段排序稳定；受保护属性绝不进入）。"""
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
            "gap_level": c.gap_level,
            "evidence_refs": list(c.evidence_refs_json or []),
        }
        for c in sorted(components, key=lambda c: c.component)
    ]
    return {
        "input_version": TAILOR_INPUT_VERSION,
        "template_id": TEMPLATE_ID,
        "facts": fact_items,
        "job": {
            "title": job_title,
            "company": company_name,
            "description": posting.description_text or "",
            "salary_raw": posting.salary_raw or "",
        },
        "rule_score": {
            "total": recommendation.score_total,
            "grade": recommendation.grade,
            "components": component_items,
        },
    }


def build_tailor_request(input_doc: dict[str, Any]) -> LLMRequest:
    """输入文档 → Gateway 请求（提示词正文绝不入日志）。"""
    payload = json.dumps(input_doc, ensure_ascii=False, sort_keys=True)
    user = (
        "请基于以下输入文档产出岗位定制简历 JSON。\n"
        "输出必须是单个 JSON 对象，严格符合以下 JSON Schema：不得增删字段、"
        "不得改变嵌套结构，数组元素必须是 Schema 定义的对象而不是字符串。\n"
        f"<OUTPUT_SCHEMA>\n{_OUTPUT_SCHEMA_JSON}\n</OUTPUT_SCHEMA>\n"
        f"{INPUT_START}\n{payload}\n{INPUT_END}"
    )
    return LLMRequest(
        system=_SYSTEM_PROMPT,
        user=user,
        schema_name="resume_tailor_v1",
        max_output_tokens=_TAILOR_MAX_OUTPUT_TOKENS,
    )


def extract_tailor_input(user_message: str) -> dict[str, Any] | None:
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
