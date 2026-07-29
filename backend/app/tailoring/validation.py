"""定制简历确定性校验（导出前 / 草稿落库前 / 用户编辑后都要过）。

红线（docs/07 第 10 节）：
- 每条内容必须绑定真实存在的已确认事实（fact_ids ⊆ active profile_facts，
  受保护属性类型的事实不在允许集合内 → 引用即违规）；
- 条目文本中的数字必须来自所引用事实的原值（防夸大/编数字）；
- 禁止 `XX%` 之类未确认占位符进入内容；
- changes 的 job_span 必须逐字来自岗位原文或输入证据片段（防编造"原文"）。

校验失败返回违规项列表（只含定位与类别，不含正文，可安全入日志）。
"""

import json
import re
from typing import Any

from app.tailoring.schemas import ResumeChange, ResumeContent

_DIGIT_RUN_RE = re.compile(r"\d+")
_PLACEHOLDER_RE = re.compile(r"(?i)xx\s*%|［?待补充］?数字|＿＿+")


def fact_value_text(value_json: dict[str, Any] | None) -> str:
    """事实值的展平文本（数字一致性校验的比对基底）。"""
    return json.dumps(value_json or {}, ensure_ascii=False, sort_keys=True)


def validate_resume_content(
    content: ResumeContent,
    changes: list[ResumeChange],
    *,
    allowed_facts: dict[str, str],
    job_text: str,
    allowed_spans: set[str],
) -> list[str]:
    """返回违规项列表（空表 = 通过）。violation 形如 ``sections[0].items[1]:kind``。"""
    violations: list[str] = []

    for si, section in enumerate(content.sections):
        for ii, item in enumerate(section.items):
            loc = f"sections[{si}].items[{ii}]"
            unknown = [fid for fid in item.fact_ids if fid not in allowed_facts]
            if unknown:
                violations.append(f"{loc}:unknown_fact_ids")
                continue
            if _PLACEHOLDER_RE.search(item.text):
                violations.append(f"{loc}:unconfirmed_placeholder")
            cited_text = "\n".join(allowed_facts[fid] for fid in item.fact_ids)
            for run in _DIGIT_RUN_RE.findall(item.text):
                if run not in cited_text:
                    violations.append(f"{loc}:number_not_in_facts")
                    break

    for ci, change in enumerate(changes):
        loc = f"changes[{ci}]"
        unknown = [fid for fid in change.fact_ids if fid not in allowed_facts]
        if unknown:
            violations.append(f"{loc}:unknown_fact_ids")
        span = change.job_span
        if span and span not in job_text and span not in allowed_spans:
            violations.append(f"{loc}:fabricated_job_span")

    return violations
