"""硬条件引擎单元测试：全分支三态 + 证据（离线，不连 DB）。"""

import uuid
from types import SimpleNamespace

from app.matching.hard_filters import (
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_PASSED_WITH_PENALTY,
    STATUS_UNKNOWN,
    PlanContext,
    evaluate_hard_conditions,
    summarize_hard_status,
)


def make_plan(**overrides) -> PlanContext:
    defaults = dict(
        city_codes=["110100"],  # 北京
        work_modes=["onsite", "hybrid"],
        minimum_monthly_salary=20000,
        allow_outsourcing=False,
        blocked_company_ids=set(),
        blocked_company_names={},
        user_education_rank=2,  # bachelor
        user_education_level="bachelor",
        user_years_experience=3.0,
    )
    defaults.update(overrides)
    return PlanContext(**defaults)


def make_posting(**overrides) -> SimpleNamespace:
    defaults = dict(
        city_code="110100",
        city_kind="city",
        work_mode=None,
        employment_type="full_time",
        salary_min=25000,
        salary_max=40000,
        salary_unknown=False,
        salary_raw="25-40K",
        experience_min=1,
        experience_max=3,
        experience_type="range",
        education_level="bachelor",
        education_requirement_type="required",
        outsourcing_signals=[],
        company_id=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def result_of(results, condition):
    return next(r for r in results if r.condition == condition)


def test_all_conditions_return_status_and_evidence():
    """每项硬条件必须有三态状态与非空证据——绝无裸布尔。"""
    results = evaluate_hard_conditions(make_plan(), make_posting())
    assert {r.condition for r in results} == {
        "city",
        "work_mode",
        "salary",
        "full_time",
        "outsourcing",
        "blocked_company",
        "education",
        "experience",
    }
    for r in results:
        assert r.status in (
            STATUS_PASSED,
            STATUS_PASSED_WITH_PENALTY,
            STATUS_FAILED,
            STATUS_UNKNOWN,
        )
        assert isinstance(r.evidence, str) and r.evidence  # 证据必须存在
    assert summarize_hard_status(results) == "passed"


# ---------------- 城市 ----------------


def test_city_mismatch_failed_and_remote_branches():
    results = evaluate_hard_conditions(
        make_plan(), make_posting(city_code="310100")  # 上海
    )
    assert result_of(results, "city").status == STATUS_FAILED
    # 远程岗位 + 方案接受远程 → passed
    plan_remote = make_plan(work_modes=["remote"])
    posting_remote = make_posting(city_code=None, city_kind="remote")
    results = evaluate_hard_conditions(plan_remote, posting_remote)
    assert result_of(results, "city").status == STATUS_PASSED
    # 远程岗位 + 方案不接受远程 → failed
    results = evaluate_hard_conditions(make_plan(), posting_remote)
    assert result_of(results, "city").status == STATUS_FAILED
    # 城市缺失 → unknown（进不确定队列，不淘汰）
    results = evaluate_hard_conditions(
        make_plan(), make_posting(city_code=None, city_kind="unknown")
    )
    assert result_of(results, "city").status == STATUS_UNKNOWN


# ---------------- 薪资：unknown 不误放也不误杀 ----------------


def test_salary_unknown_is_three_state_not_boolean():
    posting = make_posting(
        salary_min=None, salary_max=None, salary_unknown=True, salary_raw="面议"
    )
    results = evaluate_hard_conditions(make_plan(), posting)
    salary = result_of(results, "salary")
    assert salary.status == STATUS_UNKNOWN  # 不伪造通过
    assert "不确定" in salary.evidence or "未知" in salary.evidence
    assert summarize_hard_status(results) == "uncertain"  # 不淘汰：进不确定队列


def test_salary_below_minimum_failed_with_evidence():
    posting = make_posting(salary_min=10000, salary_max=15000)
    results = evaluate_hard_conditions(make_plan(minimum_monthly_salary=20000), posting)
    salary = result_of(results, "salary")
    assert salary.status == STATUS_FAILED
    assert "15000" in salary.evidence and "20000" in salary.evidence


def test_salary_no_plan_minimum_passes():
    results = evaluate_hard_conditions(
        make_plan(minimum_monthly_salary=None),
        make_posting(salary_unknown=True, salary_min=None, salary_max=None),
    )
    assert result_of(results, "salary").status == STATUS_PASSED


# ---------------- 全职 ----------------


def test_full_time_branches():
    assert (
        result_of(
            evaluate_hard_conditions(make_plan(), make_posting(employment_type="other")),
            "full_time",
        ).status
        == STATUS_FAILED
    )
    assert (
        result_of(
            evaluate_hard_conditions(make_plan(), make_posting(employment_type="unknown")),
            "full_time",
        ).status
        == STATUS_UNKNOWN
    )


# ---------------- 外包 ----------------


def test_outsourcing_high_confidence_failed_low_confidence_unknown():
    high = [
        {
            "code": "POSSIBLE_OUTSOURCING",
            "keyword": "外包",
            "evidence": "title: …外包…",
            "confidence": 0.9,
        }
    ]
    low = [
        {
            "code": "POSSIBLE_OUTSOURCING",
            "keyword": "驻场",
            "evidence": "description: …驻场…",
            "confidence": 0.7,
        }
    ]
    r_high = result_of(
        evaluate_hard_conditions(make_plan(), make_posting(outsourcing_signals=high)),
        "outsourcing",
    )
    assert r_high.status == STATUS_FAILED
    assert "外包" in r_high.evidence
    r_low = result_of(
        evaluate_hard_conditions(make_plan(), make_posting(outsourcing_signals=low)),
        "outsourcing",
    )
    assert r_low.status == STATUS_UNKNOWN  # 低置信只展示不淘汰
    # 方案开启外包 → 高置信信号也放行
    r_allowed = result_of(
        evaluate_hard_conditions(
            make_plan(allow_outsourcing=True), make_posting(outsourcing_signals=high)
        ),
        "outsourcing",
    )
    assert r_allowed.status == STATUS_PASSED


# ---------------- 屏蔽公司 ----------------


def test_blocked_company_failed():
    company_id = uuid.uuid4()
    plan = make_plan(
        blocked_company_ids={company_id}, blocked_company_names={company_id: "某某外包公司"}
    )
    results = evaluate_hard_conditions(plan, make_posting(company_id=company_id))
    blocked = result_of(results, "blocked_company")
    assert blocked.status == STATUS_FAILED
    assert "某某外包公司" in blocked.evidence
    assert summarize_hard_status(results) == "failed"


# ---------------- 学历两级 ----------------


def test_education_required_vs_preferred():
    # required 且不满足 → failed
    posting = make_posting(education_level="master", education_requirement_type="required")
    results = evaluate_hard_conditions(make_plan(), posting)
    assert result_of(results, "education").status == STATUS_FAILED
    # preferred 不满足 → 硬条件放行（评分阶段降分）
    posting = make_posting(education_level="master", education_requirement_type="preferred")
    results = evaluate_hard_conditions(make_plan(), posting)
    assert result_of(results, "education").status == STATUS_PASSED
    # required 但用户学历未知 → unknown
    results = evaluate_hard_conditions(
        make_plan(user_education_rank=None, user_education_level=None),
        make_posting(education_level="bachelor", education_requirement_type="required"),
    )
    assert result_of(results, "education").status == STATUS_UNKNOWN


# ---------------- 经验 ----------------


def test_experience_gap_branches():
    # 满足 → passed
    r = result_of(
        evaluate_hard_conditions(make_plan(user_years_experience=3.0), make_posting()),
        "experience",
    )
    assert r.status == STATUS_PASSED
    # 差 1 年以内 → passed_with_penalty（降分进入）
    r = result_of(
        evaluate_hard_conditions(
            make_plan(user_years_experience=2.2),
            make_posting(experience_min=3, experience_max=5),
        ),
        "experience",
    )
    assert r.status == STATUS_PASSED_WITH_PENALTY
    # 差距 >1 年 → failed
    r = result_of(
        evaluate_hard_conditions(
            make_plan(user_years_experience=1.0),
            make_posting(experience_min=5, experience_max=10),
        ),
        "experience",
    )
    assert r.status == STATUS_FAILED
    # 用户经验未知 + 岗位有要求 → unknown
    r = result_of(
        evaluate_hard_conditions(make_plan(user_years_experience=None), make_posting()),
        "experience",
    )
    assert r.status == STATUS_UNKNOWN
    # 经验不限 → passed
    r = result_of(
        evaluate_hard_conditions(
            make_plan(user_years_experience=None),
            make_posting(experience_min=None, experience_max=None, experience_type="unrestricted"),
        ),
        "experience",
    )
    assert r.status == STATUS_PASSED
