"""用户画像派生：从已确认事实（profile_facts）计算学历等级、经验年限与技能列表。

只使用 **已确认** 事实；缺失时如实返回 None（下游按 unknown 三态处理，不猜测）。
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.matching.constants import DEGREE_TEXT_TO_LEVEL, EDUCATION_RANK

_YM_RE = re.compile(r"((?:19|20)\d{2})\s*[./年]\s*(\d{1,2})")


@dataclass(frozen=True)
class FactRef:
    """通用事实引用（证据引用用 fact_id）。"""

    fact_id: str
    fact_type: str
    text: str  # 供词面匹配的拼接文本（name/detail/company 等，非正文原文）


@dataclass
class UserProfile:
    """匹配用用户画像；全部字段可缺失（None → 硬条件 unknown）。"""

    education_level: str | None = None  # associate/bachelor/master/phd
    education_rank: int | None = None
    education_fact_ids: list[str] = field(default_factory=list)
    years_experience: float | None = None
    work_fact_ids: list[str] = field(default_factory=list)
    skill_refs: list[FactRef] = field(default_factory=list)
    detail_refs: list[FactRef] = field(default_factory=list)  # 项目证据用（skill detail 等）


def _parse_ym(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = _YM_RE.search(raw)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return datetime(year, month, 1, tzinfo=UTC)


def _months_between(start: datetime, end: datetime) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def build_user_profile(facts: list) -> UserProfile:
    """facts: 已确认 ProfileFact（或同构对象，含 id/fact_type/value_json）。

    - 学历取最高学位；经验年限按工作时间段合并重叠后求和。
    - 时间段解析失败的经历不计入年限（不猜测）。
    """
    profile = UserProfile()
    now = datetime.now(UTC)
    periods: list[tuple[datetime, datetime]] = []

    for fact in facts:
        fact_id = str(fact.id)
        value = fact.value_json or {}
        if fact.fact_type == "education":
            degree = str(value.get("degree", ""))
            level = DEGREE_TEXT_TO_LEVEL.get(degree)
            if level is not None:
                rank = EDUCATION_RANK[level]
                if profile.education_rank is None or rank > profile.education_rank:
                    profile.education_level = level
                    profile.education_rank = rank
                profile.education_fact_ids.append(fact_id)
        elif fact.fact_type == "work_experience":
            profile.work_fact_ids.append(fact_id)
            start = _parse_ym(str(value.get("start_raw", "")))
            end_raw = str(value.get("end_raw", ""))
            end = now if value.get("is_current") or end_raw in ("至今", "现在") else _parse_ym(
                end_raw
            )
            if start is not None and end is not None and end >= start:
                periods.append((start, end))
            company = str(value.get("company", ""))
            if company:
                profile.detail_refs.append(FactRef(fact_id, "work_experience", company))
        elif fact.fact_type == "skill":
            name = str(value.get("name", ""))
            detail = str(value.get("detail", ""))
            text = f"{name} {detail}".strip()
            if text:
                profile.skill_refs.append(FactRef(fact_id, "skill", text))
            if detail:
                profile.detail_refs.append(FactRef(fact_id, "skill", detail))

    if periods:
        # 合并重叠时间段后求总月数
        periods.sort()
        merged: list[tuple[datetime, datetime]] = [periods[0]]
        for start, end in periods[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        total_months = sum(_months_between(s, e) for s, e in merged)
        profile.years_experience = round(total_months / 12, 2)

    return profile
