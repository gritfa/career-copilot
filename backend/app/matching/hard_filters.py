"""硬条件引擎（docs/07 第 2 节）：每项返回 passed/failed/unknown + 证据，绝无裸布尔。

- 薪资缺失/面议 → unknown：不淘汰、不伪造通过，进不确定队列。
- 外包：方案未开启时高置信信号（标题命中）→ failed；低置信 → unknown 展示。
- 学历 required 且不满足 → failed；preferred → 只降分（评分阶段处理）。
- 经验差 >1 年 → failed；≤1 年 → passed_with_penalty（降分进入）。
- failed 必须有证据；信息缺失一律 unknown，不允许“猜测通过”。
"""

from dataclasses import dataclass, field
from typing import Any

from app.jobs.constants import CITY_NAME_BY_CODE
from app.matching.constants import (
    EDUCATION_RANK,
    EXPERIENCE_PENALTY_MAX_GAP_YEARS,
    OUTSOURCING_FAIL_CONFIDENCE,
)

# 逐项状态：passed_with_penalty 计入 passed（过滤意义上），但保留降分标记
STATUS_PASSED = "passed"
STATUS_PASSED_WITH_PENALTY = "passed_with_penalty"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class HardConditionResult:
    """单项硬条件结果：三态 + 证据 + 结构化明细。"""

    condition: str
    status: str
    evidence: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "status": self.status,
            "evidence": self.evidence,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PlanContext:
    """硬条件评估所需的方案侧上下文。"""

    city_codes: list[str]
    work_modes: list[str]
    minimum_monthly_salary: int | None
    allow_outsourcing: bool
    blocked_company_ids: set
    blocked_company_names: dict  # company_id -> name（证据展示用）
    user_education_rank: int | None
    user_education_level: str | None
    user_years_experience: float | None


def _city_names(codes: list[str]) -> str:
    return "/".join(CITY_NAME_BY_CODE.get(c, c) for c in codes)


def check_city(plan: PlanContext, posting) -> HardConditionResult:
    if posting.city_kind == "remote":
        if "remote" in plan.work_modes:
            return HardConditionResult(
                "city", STATUS_PASSED, "远程岗位且方案接受远程办公", {"city_kind": "remote"}
            )
        return HardConditionResult(
            "city",
            STATUS_FAILED,
            "远程岗位，但方案办公方式未包含远程",
            {"city_kind": "remote", "plan_work_modes": plan.work_modes},
        )
    if posting.city_kind == "nationwide":
        return HardConditionResult(
            "city", STATUS_PASSED, "岗位覆盖全国/多城市", {"city_kind": "nationwide"}
        )
    if posting.city_kind == "city" and posting.city_code:
        if posting.city_code in plan.city_codes:
            return HardConditionResult(
                "city",
                STATUS_PASSED,
                f"岗位城市 {CITY_NAME_BY_CODE.get(posting.city_code, posting.city_code)} "
                f"在方案城市（{_city_names(plan.city_codes)}）内",
                {"city_code": posting.city_code},
            )
        return HardConditionResult(
            "city",
            STATUS_FAILED,
            f"岗位城市 {CITY_NAME_BY_CODE.get(posting.city_code, posting.city_code)} "
            f"不在方案城市（{_city_names(plan.city_codes)}）内",
            {"city_code": posting.city_code, "plan_city_codes": plan.city_codes},
        )
    return HardConditionResult(
        "city", STATUS_UNKNOWN, "岗位城市信息缺失或无法识别", {"city_kind": posting.city_kind}
    )


def check_work_mode(plan: PlanContext, posting) -> HardConditionResult:
    effective = posting.work_mode
    inferred = False
    if effective is None:
        if posting.city_kind == "remote":
            effective, inferred = "remote", True
        elif posting.city_kind == "city":
            # 中国大陆岗位默认到岗（onsite）；这是推断，证据里如实说明
            effective, inferred = "onsite", True
    if effective is None:
        return HardConditionResult(
            "work_mode", STATUS_UNKNOWN, "岗位未标注办公方式且无法从城市信息推断", {}
        )
    if effective in plan.work_modes or (
        effective == "onsite" and "hybrid" in plan.work_modes
    ):
        return HardConditionResult(
            "work_mode",
            STATUS_PASSED,
            f"办公方式 {effective}{'（按城市岗位推断）' if inferred else ''}符合方案要求",
            {"effective_mode": effective, "inferred": inferred},
        )
    return HardConditionResult(
        "work_mode",
        STATUS_FAILED,
        f"办公方式 {effective}{'（按城市岗位推断）' if inferred else ''}"
        f"不在方案要求（{'/'.join(plan.work_modes)}）内",
        {"effective_mode": effective, "inferred": inferred, "plan_work_modes": plan.work_modes},
    )


def check_salary(plan: PlanContext, posting) -> HardConditionResult:
    if plan.minimum_monthly_salary is None:
        return HardConditionResult("salary", STATUS_PASSED, "方案未设置最低薪资要求", {})
    if posting.salary_unknown or (posting.salary_min is None and posting.salary_max is None):
        # 面议/缺失：进不确定队列，不淘汰也不伪造通过（docs/07 第 2 节）
        return HardConditionResult(
            "salary",
            STATUS_UNKNOWN,
            f"岗位薪资未知（{posting.salary_raw or '未标注'}），进入不确定队列",
            {"salary_raw": posting.salary_raw, "salary_unknown": True},
        )
    ceiling = posting.salary_max if posting.salary_max is not None else posting.salary_min
    if ceiling is not None and ceiling < plan.minimum_monthly_salary:
        return HardConditionResult(
            "salary",
            STATUS_FAILED,
            f"岗位薪资上限 {ceiling} 低于方案最低月薪 {plan.minimum_monthly_salary}",
            {"salary_min": posting.salary_min, "salary_max": posting.salary_max},
        )
    return HardConditionResult(
        "salary",
        STATUS_PASSED,
        f"岗位薪资 {posting.salary_min}-{posting.salary_max} "
        f"满足方案最低月薪 {plan.minimum_monthly_salary}",
        {"salary_min": posting.salary_min, "salary_max": posting.salary_max},
    )


def check_full_time(plan: PlanContext, posting) -> HardConditionResult:
    if posting.employment_type == "full_time":
        return HardConditionResult("full_time", STATUS_PASSED, "全职岗位", {})
    if posting.employment_type == "other":
        return HardConditionResult(
            "full_time",
            STATUS_FAILED,
            "非全职岗位（实习/兼职/劳务等），仅支持全职",
            {"employment_type": posting.employment_type},
        )
    return HardConditionResult(
        "full_time", STATUS_UNKNOWN, "用工类型未标注", {"employment_type": posting.employment_type}
    )


def check_outsourcing(plan: PlanContext, posting) -> HardConditionResult:
    signals = list(posting.outsourcing_signals or [])
    if plan.allow_outsourcing:
        return HardConditionResult(
            "outsourcing", STATUS_PASSED, "方案已允许外包/派遣岗位", {"signals": signals}
        )
    if not signals:
        return HardConditionResult(
            "outsourcing", STATUS_PASSED, "未检测到外包/派遣/驻场信号", {}
        )
    top = max(signals, key=lambda s: s.get("confidence", 0))
    if top.get("confidence", 0) >= OUTSOURCING_FAIL_CONFIDENCE:
        return HardConditionResult(
            "outsourcing",
            STATUS_FAILED,
            f"高置信外包/派遣信号：{top.get('evidence', '')}",
            {"signals": signals, "threshold": OUTSOURCING_FAIL_CONFIDENCE},
        )
    return HardConditionResult(
        "outsourcing",
        STATUS_UNKNOWN,
        f"低置信外包/派遣信号（仅展示不淘汰）：{top.get('evidence', '')}",
        {"signals": signals, "threshold": OUTSOURCING_FAIL_CONFIDENCE},
    )


def check_blocked_company(plan: PlanContext, posting) -> HardConditionResult:
    company_id = getattr(posting, "company_id", None)
    if company_id is not None and company_id in plan.blocked_company_ids:
        name = plan.blocked_company_names.get(company_id, str(company_id))
        return HardConditionResult(
            "blocked_company",
            STATUS_FAILED,
            f"公司「{name}」在方案屏蔽列表中（屏蔽优先级高于算法分数）",
            {"company_id": str(company_id)},
        )
    return HardConditionResult("blocked_company", STATUS_PASSED, "公司不在屏蔽列表", {})


def check_education(plan: PlanContext, posting) -> HardConditionResult:
    req_type = posting.education_requirement_type
    level = posting.education_level
    if req_type == "required" and level in EDUCATION_RANK:
        required_rank = EDUCATION_RANK[level]
        if plan.user_education_rank is None:
            return HardConditionResult(
                "education",
                STATUS_UNKNOWN,
                f"岗位要求学历 {level}，但事实库中无已确认学历信息",
                {"required_level": level},
            )
        if plan.user_education_rank < required_rank:
            return HardConditionResult(
                "education",
                STATUS_FAILED,
                f"岗位硬性要求学历 {level}，用户学历 {plan.user_education_level} 不满足",
                {"required_level": level, "user_level": plan.user_education_level},
            )
        return HardConditionResult(
            "education",
            STATUS_PASSED,
            f"用户学历 {plan.user_education_level} 满足岗位要求 {level}",
            {"required_level": level, "user_level": plan.user_education_level},
        )
    if req_type == "preferred":
        # preferred 不做硬过滤，只在评分阶段降分（docs/07 第 2 节）
        return HardConditionResult(
            "education",
            STATUS_PASSED,
            f"学历为优先条件（{level or '未识别'}），不做硬过滤，仅参与降分",
            {"required_level": level, "requirement_type": "preferred"},
        )
    return HardConditionResult(
        "education", STATUS_PASSED, "未识别到硬性学历要求", {"requirement_type": req_type}
    )


def check_experience(plan: PlanContext, posting) -> HardConditionResult:
    exp_type = posting.experience_type
    if exp_type in ("unrestricted", "fresh_grad"):
        return HardConditionResult(
            "experience", STATUS_PASSED, "岗位经验不限或面向应届", {"experience_type": exp_type}
        )
    if exp_type != "range" or posting.experience_min is None:
        return HardConditionResult(
            "experience", STATUS_PASSED, "未标注经验要求，不做硬过滤", {"experience_type": exp_type}
        )
    if plan.user_years_experience is None:
        return HardConditionResult(
            "experience",
            STATUS_UNKNOWN,
            f"岗位要求 {posting.experience_min} 年以上经验，"
            "但事实库中无可计算的已确认工作经历",
            {"experience_min": posting.experience_min},
        )
    gap = posting.experience_min - plan.user_years_experience
    if gap > EXPERIENCE_PENALTY_MAX_GAP_YEARS:
        return HardConditionResult(
            "experience",
            STATUS_FAILED,
            f"经验差距过大：岗位要求 {posting.experience_min} 年，"
            f"用户约 {plan.user_years_experience} 年（差 {round(gap, 2)} 年 > 1 年）",
            {"experience_min": posting.experience_min, "user_years": plan.user_years_experience},
        )
    if gap > 0:
        return HardConditionResult(
            "experience",
            STATUS_PASSED_WITH_PENALTY,
            f"经验差 {round(gap, 2)} 年（≤1 年），降分进入",
            {"experience_min": posting.experience_min, "user_years": plan.user_years_experience},
        )
    return HardConditionResult(
        "experience",
        STATUS_PASSED,
        f"用户约 {plan.user_years_experience} 年经验满足岗位要求 "
        f"{posting.experience_min} 年",
        {"experience_min": posting.experience_min, "user_years": plan.user_years_experience},
    )


_CHECKS = (
    check_city,
    check_work_mode,
    check_salary,
    check_full_time,
    check_outsourcing,
    check_blocked_company,
    check_education,
    check_experience,
)


def evaluate_hard_conditions(plan: PlanContext, posting) -> list[HardConditionResult]:
    """按方案执行全部硬条件；顺序固定，结果可复现。"""
    return [check(plan, posting) for check in _CHECKS]


def summarize_hard_status(results: list[HardConditionResult]) -> str:
    """整体状态：任一 failed → failed；否则任一 unknown → uncertain；否则 passed。"""
    statuses = {r.status for r in results}
    if STATUS_FAILED in statuses:
        return "failed"
    if STATUS_UNKNOWN in statuses:
        return "uncertain"
    return "passed"
