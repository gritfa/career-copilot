"""综合评分 v1（docs/07 第 4、5 节）。

原则：
- 每个分项要么带 evidence_refs（fact_id + 岗位文本 span），要么显式
  ``uncertainty="insufficient_evidence"``——找不到证据绝不让规则编造。
- 词面匹配是确定性规则，可复现；不调用任何 LLM。
- 学习偏好（learned_preferences）只影响用户偏好分项（10 分），
  不覆盖用户显式配置（最低薪资等硬条件在硬过滤阶段执行）。
- 风险信号（外包等）不混入能力分，单独在 API 层展示。
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.matching.constants import (
    COMPONENT_WEIGHTS,
    INDUSTRY_TERMS,
    SCORING_VERSION,
    TECH_TERMS,
    grade_for,
)
from app.matching.hard_filters import (
    STATUS_PASSED_WITH_PENALTY,
    HardConditionResult,
)
from app.matching.profile import UserProfile

INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass
class ComponentScore:
    component: str
    score: int  # 0-100
    weight: int
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    gap_level: str = "unknown"
    uncertainty: str | None = None


@dataclass
class ScoringResult:
    score_total: int
    grade: str
    scoring_version: str
    components: list[ComponentScore]


def _clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def _job_span(text: str, term: str, width: int = 40) -> str:
    """在岗位文本中定位 term 的最小上下文片段（证据 span）。"""
    lowered = text.lower()
    idx = lowered.find(term.lower())
    if idx < 0:
        return ""
    start = max(0, idx - width)
    end = min(len(text), idx + len(term) + width)
    return text[start:end].strip()


def term_in(term: str, text: str) -> bool:
    """确定性词面匹配：ASCII 词要求词边界（避免 go 命中 django），中文短语用子串。"""
    if term.isascii():
        pattern = r"(?<![a-z0-9+#.])" + re.escape(term) + r"(?![a-z0-9+#.])"
        return re.search(pattern, text) is not None
    return term in text


def _job_terms(posting) -> list[str]:
    """岗位文本中出现的技术词（确定性词表匹配，按词表顺序稳定输出）。"""
    text = f"{posting.title_raw or ''} {posting.description_text or ''}".lower()
    found = []
    for term in TECH_TERMS:
        if term_in(term, text):
            # 避免 "spring" 与 "spring boot" 重复计数：保留更长的短语，
            # 短词已被更长短语覆盖时跳过
            if any(
                term != other and term in other and term_in(other, text)
                for other in TECH_TERMS
            ):
                continue
            found.append(term)
    return found


def score_core_skills(profile: UserProfile, posting) -> ComponentScore:
    """核心技能（35）：岗位技术词 × 已确认技能事实的词面覆盖率。"""
    job_terms = _job_terms(posting)
    if not job_terms:
        return ComponentScore(
            "core_skills",
            0,
            COMPONENT_WEIGHTS["core_skills"],
            uncertainty=INSUFFICIENT_EVIDENCE,
            gap_level="unknown",
        )
    description = f"{posting.title_raw or ''}\n{posting.description_text or ''}"
    evidence: list[dict[str, Any]] = []
    matched: list[str] = []
    missing: list[str] = []
    for term in job_terms:
        fact_ids = [
            ref.fact_id for ref in profile.skill_refs if term_in(term, ref.text.lower())
        ]
        if fact_ids:
            matched.append(term)
            evidence.append(
                {
                    "claim": f"具备 {term} 相关技能",
                    "profile_fact_ids": fact_ids,
                    "job_evidence": {"span": _job_span(description, term)},
                    "strength": "strong",
                    "uncertainty": None,
                }
            )
        else:
            missing.append(term)
    if missing:
        # 缺口也是证据（岗位 span 支撑），不是编造
        evidence.append(
            {
                "claim": "岗位要求但事实库无证据的技能",
                "profile_fact_ids": [],
                "job_evidence": {
                    "span": "; ".join(_job_span(description, t, 15) for t in missing[:5])
                },
                "missing_terms": missing,
                "strength": "gap",
                "uncertainty": None,
            }
        )
    ratio = len(matched) / len(job_terms)
    gap = "none" if ratio >= 0.8 else "minor" if ratio >= 0.5 else "major"
    return ComponentScore(
        "core_skills",
        _clamp(100 * ratio),
        COMPONENT_WEIGHTS["core_skills"],
        evidence_refs=evidence,
        gap_level=gap,
    )


def score_experience(
    profile: UserProfile, posting, hard_results: list[HardConditionResult]
) -> ComponentScore:
    """工作经验（20）：年限对照 + 经验差 1 年内降分 + preferred 学历未满足降分。"""
    weight = COMPONENT_WEIGHTS["experience"]
    if profile.years_experience is None:
        return ComponentScore(
            "experience", 0, weight, uncertainty=INSUFFICIENT_EVIDENCE, gap_level="unknown"
        )
    evidence: list[dict[str, Any]] = []
    penalty_hit = any(
        r.condition == "experience" and r.status == STATUS_PASSED_WITH_PENALTY
        for r in hard_results
    )
    if posting.experience_type == "range" and posting.experience_min is not None:
        if penalty_hit:
            score = 50  # 经验差 ≤1 年：降分进入（docs/07 第 2 节）
            gap = "minor"
            claim = (
                f"经验约 {profile.years_experience} 年，略低于岗位要求 "
                f"{posting.experience_min} 年（差距 ≤1 年，降分）"
            )
        else:
            in_range = (
                posting.experience_max is None
                or profile.years_experience <= posting.experience_max + 1
            )
            score = 100 if in_range else 90  # 超出上限：轻微折让，不视为缺口
            gap = "none"
            claim = (
                f"经验约 {profile.years_experience} 年，满足岗位要求 "
                f"{posting.experience_min} 年以上"
            )
        evidence.append(
            {
                "claim": claim,
                "profile_fact_ids": profile.work_fact_ids,
                "job_evidence": {
                    "span": f"经验要求 {posting.experience_min}"
                    + (f"-{posting.experience_max}" if posting.experience_max else "+")
                    + " 年"
                },
                "strength": "strong",
                "uncertainty": None,
            }
        )
    else:
        score = 80  # 岗位未量化经验要求：凭已确认经历给基准分（证据为经历事实）
        gap = "none"
        evidence.append(
            {
                "claim": f"已确认工作经历约 {profile.years_experience} 年；岗位未量化经验要求",
                "profile_fact_ids": profile.work_fact_ids,
                "job_evidence": {"span": ""},
                "strength": "moderate",
                "uncertainty": None,
            }
        )
    # 学历 preferred 未满足 → 只降分（硬过滤已放行）
    for r in hard_results:
        if (
            r.condition == "education"
            and r.detail.get("requirement_type") == "preferred"
            and r.detail.get("required_level")
        ):
            from app.matching.constants import EDUCATION_RANK

            required_rank = EDUCATION_RANK.get(r.detail["required_level"])
            user_rank = (
                None
                if profile.education_rank is None
                else profile.education_rank
            )
            if required_rank is not None and (user_rank is None or user_rank < required_rank):
                score = max(0, score - 20)
                gap = "minor" if gap == "none" else gap
                evidence.append(
                    {
                        "claim": f"岗位优先学历 {r.detail['required_level']} 未满足（仅降分）",
                        "profile_fact_ids": profile.education_fact_ids,
                        "job_evidence": {"span": r.evidence},
                        "strength": "moderate",
                        "uncertainty": None,
                    }
                )
    return ComponentScore(
        "experience", _clamp(score), weight, evidence_refs=evidence, gap_level=gap
    )


def score_project_evidence(profile: UserProfile, posting) -> ComponentScore:
    """项目证据（20）：技能 detail/经历文本与岗位职责的词面对应。"""
    weight = COMPONENT_WEIGHTS["project_evidence"]
    if not profile.detail_refs:
        return ComponentScore(
            "project_evidence", 0, weight, uncertainty=INSUFFICIENT_EVIDENCE, gap_level="unknown"
        )
    job_terms = _job_terms(posting)
    if not job_terms:
        return ComponentScore(
            "project_evidence", 0, weight, uncertainty=INSUFFICIENT_EVIDENCE, gap_level="unknown"
        )
    description = f"{posting.title_raw or ''}\n{posting.description_text or ''}"
    evidence: list[dict[str, Any]] = []
    matched_terms: set[str] = set()
    for term in job_terms:
        fact_ids = [
            ref.fact_id for ref in profile.detail_refs if term_in(term, ref.text.lower())
        ]
        if fact_ids:
            matched_terms.add(term)
            evidence.append(
                {
                    "claim": f"项目/经历细节可对应岗位职责中的 {term}",
                    "profile_fact_ids": fact_ids,
                    "job_evidence": {"span": _job_span(description, term)},
                    "strength": "moderate",
                    "uncertainty": None,
                }
            )
    if not evidence:
        return ComponentScore(
            "project_evidence", 0, weight, uncertainty=INSUFFICIENT_EVIDENCE, gap_level="major"
        )
    score = _clamp(100 * len(matched_terms) / len(job_terms) + 20)
    gap = "none" if len(matched_terms) / len(job_terms) >= 0.5 else "minor"
    return ComponentScore(
        "project_evidence", score, weight, evidence_refs=evidence, gap_level=gap
    )


def score_role_semantic(
    plan_role_family: str, posting, cosine_similarity: float | None
) -> ComponentScore:
    """岗位方向语义（10）：职位族一致性；岗位方向未知时用向量相似度。"""
    weight = COMPONENT_WEIGHTS["role_semantic"]
    if posting.role_family == plan_role_family:
        return ComponentScore(
            "role_semantic",
            100,
            weight,
            evidence_refs=[
                {
                    "claim": f"岗位方向 {posting.role_family} 与方案一致",
                    "profile_fact_ids": [],
                    "job_evidence": {"span": posting.title_raw or ""},
                    "strength": "strong",
                    "uncertainty": None,
                }
            ],
            gap_level="none",
        )
    if posting.role_family == "unknown":
        if cosine_similarity is None:
            return ComponentScore(
                "role_semantic",
                0,
                weight,
                uncertainty=INSUFFICIENT_EVIDENCE,
                gap_level="unknown",
            )
        return ComponentScore(
            "role_semantic",
            _clamp(cosine_similarity * 100),
            weight,
            evidence_refs=[
                {
                    "claim": "岗位方向未识别，使用向量余弦相似度作为方向语义参考",
                    "profile_fact_ids": [],
                    "job_evidence": {"span": posting.title_raw or ""},
                    "strength": "weak",
                    "uncertainty": "vector_only",
                }
            ],
            gap_level="unknown",
            uncertainty="vector_only",
        )
    return ComponentScore(
        "role_semantic",
        0,
        weight,
        evidence_refs=[
            {
                "claim": f"岗位方向 {posting.role_family} 与方案方向 {plan_role_family} 不一致",
                "profile_fact_ids": [],
                "job_evidence": {"span": posting.title_raw or ""},
                "strength": "strong",
                "uncertainty": None,
            }
        ],
        gap_level="major",
    )


def score_industry(profile: UserProfile, posting) -> ComponentScore:
    """行业背景（5）：仅在岗位明确写出行业/业务要求时判定；否则记满分（不适用）。"""
    weight = COMPONENT_WEIGHTS["industry"]
    text = f"{posting.title_raw or ''} {posting.description_text or ''}"
    required = [
        term
        for term in INDUSTRY_TERMS
        if f"{term}行业" in text or f"{term}业务" in text
    ]
    if not required:
        return ComponentScore(
            "industry",
            100,
            weight,
            evidence_refs=[
                {
                    "claim": "岗位未明确要求特定行业背景（条件不适用）",
                    "profile_fact_ids": [],
                    "job_evidence": {"span": ""},
                    "strength": "strong",
                    "uncertainty": None,
                }
            ],
            gap_level="none",
        )
    all_text = " ".join(
        ref.text for ref in (profile.skill_refs + profile.detail_refs)
    )
    evidence = []
    hit = []
    for term in required:
        fact_ids = [
            ref.fact_id
            for ref in profile.skill_refs + profile.detail_refs
            if term in ref.text
        ]
        if fact_ids and term in all_text:
            hit.append(term)
            evidence.append(
                {
                    "claim": f"具备 {term} 行业相关经历/技能",
                    "profile_fact_ids": sorted(set(fact_ids)),
                    "job_evidence": {"span": _job_span(text, term)},
                    "strength": "moderate",
                    "uncertainty": None,
                }
            )
    if not hit:
        return ComponentScore(
            "industry", 0, weight, uncertainty=INSUFFICIENT_EVIDENCE, gap_level="minor"
        )
    return ComponentScore(
        "industry",
        _clamp(100 * len(hit) / len(required)),
        weight,
        evidence_refs=evidence,
        gap_level="none" if len(hit) == len(required) else "minor",
    )


def score_preference(
    plan,
    posting,
    company_preference: str | None,
    learned_weights: dict[str, Any],
) -> ComponentScore:
    """用户偏好（10）：目标薪资 + 公司偏好 + 学习偏好（只在本分项生效）。"""
    weight = COMPONENT_WEIGHTS["preference"]
    evidence: list[dict[str, Any]] = []
    # 薪资期望（0-40）
    target = plan.target_monthly_salary
    if posting.salary_unknown or posting.salary_max is None:
        salary_part = 20
        evidence.append(
            {
                "claim": "岗位薪资未知，薪资偏好按中性计",
                "profile_fact_ids": [],
                "job_evidence": {"span": posting.salary_raw or "未标注"},
                "strength": "weak",
                "uncertainty": "salary_unknown",
            }
        )
    elif target is None:
        salary_part = 30
    elif posting.salary_max >= target:
        salary_part = 40
        evidence.append(
            {
                "claim": f"岗位薪资上限 {posting.salary_max} 覆盖目标月薪 {target}",
                "profile_fact_ids": [],
                "job_evidence": {"span": posting.salary_raw or ""},
                "strength": "strong",
                "uncertainty": None,
            }
        )
    else:
        salary_part = 15
        evidence.append(
            {
                "claim": f"岗位薪资上限 {posting.salary_max} 低于目标月薪 {target}",
                "profile_fact_ids": [],
                "job_evidence": {"span": posting.salary_raw or ""},
                "strength": "strong",
                "uncertainty": None,
            }
        )
    # 公司偏好（0-30）
    company_part = {"priority": 30, "follow": 25}.get(company_preference or "", 20)
    if company_preference in ("priority", "follow"):
        evidence.append(
            {
                "claim": f"公司在方案偏好列表（{company_preference}）",
                "profile_fact_ids": [],
                "job_evidence": {"span": ""},
                "strength": "strong",
                "uncertainty": None,
            }
        )
    # 学习偏好（0-30 起扣）：只影响本分项，不覆盖显式配置
    learned_part = 30
    deductions: list[str] = []
    for key, count in (learned_weights or {}).items():
        if not isinstance(count, int) or count <= 0 or not key.startswith("not_interested:"):
            continue
        reason = key.split(":", 1)[1]
        applies = False
        if reason == "salary":
            applies = (
                target is not None
                and posting.salary_max is not None
                and posting.salary_max < target
            )
        elif reason == "outsourcing":
            applies = bool(posting.outsourcing_signals)
        elif reason == "tech_direction":
            applies = posting.role_family not in (plan.role_family, "unknown")
        else:
            # 其他原因（公司/内容等）：弱全局信号，小幅累计
            learned_part -= min(6, count)
            deductions.append(f"{reason}×{count}")
            continue
        if applies:
            learned_part -= min(15, 3 * count)
            deductions.append(f"{reason}×{count}")
    learned_part = max(0, learned_part)
    if deductions:
        evidence.append(
            {
                "claim": "根据历史不感兴趣反馈下调偏好分（可在设置中一键重置）",
                "profile_fact_ids": [],
                "job_evidence": {"span": ""},
                "learned_deductions": deductions,
                "strength": "moderate",
                "uncertainty": None,
            }
        )
    score = _clamp(salary_part + company_part + learned_part)
    if not evidence:
        # 红线：分项要么带证据要么显式标注证据不足，绝不静默给分
        return ComponentScore(
            "preference",
            score,
            weight,
            uncertainty=INSUFFICIENT_EVIDENCE,
            gap_level="none" if score >= 60 else "minor",
        )
    return ComponentScore(
        "preference",
        score,
        weight,
        evidence_refs=evidence,
        gap_level="none" if score >= 60 else "minor",
    )


def compute_score(
    *,
    profile: UserProfile,
    plan,
    posting,
    hard_results: list[HardConditionResult],
    cosine_similarity: float | None,
    company_preference: str | None,
    learned_weights: dict[str, Any] | None,
) -> ScoringResult:
    """全部分项 + 加权总分（0-100）+ 等级。确定性：同输入同输出。"""
    components = [
        score_core_skills(profile, posting),
        score_experience(profile, posting, hard_results),
        score_project_evidence(profile, posting),
        score_role_semantic(plan.role_family, posting, cosine_similarity),
        score_industry(profile, posting),
        score_preference(plan, posting, company_preference, learned_weights or {}),
    ]
    total = _clamp(sum(c.score * c.weight / 100 for c in components))
    return ScoringResult(
        score_total=total,
        grade=grade_for(total),
        scoring_version=SCORING_VERSION,
        components=components,
    )
