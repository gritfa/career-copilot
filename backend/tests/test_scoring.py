"""综合评分 v1 单元测试：证据引用存在性、insufficient_evidence、受保护属性对偶。"""

from types import SimpleNamespace

from app.matching.constants import COMPONENT_WEIGHTS, SCORING_VERSION, grade_for
from app.matching.hard_filters import PlanContext, evaluate_hard_conditions
from app.matching.profile import build_user_profile
from app.matching.scoring import INSUFFICIENT_EVIDENCE, compute_score
from app.resumes.parser import RuleBasedExtractor, filter_protected


def make_plan_obj(**overrides):
    defaults = dict(
        role_family="backend_python",
        city_codes=["110100"],
        work_modes=["onsite", "hybrid"],
        minimum_monthly_salary=None,
        target_monthly_salary=25000,
        allow_outsourcing=False,
        minimum_match_score=65,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_posting(**overrides):
    defaults = dict(
        title_raw="Python 后端开发工程师",
        title_normalized="python后端开发工程师",
        role_family="backend_python",
        description_text=(
            "负责核心服务的 FastAPI 开发，PostgreSQL 与 Redis 调优，"
            "参与 Docker 部署与 CI 建设。"
        ),
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


def make_fact(fact_id, fact_type, value):
    return SimpleNamespace(id=fact_id, fact_type=fact_type, value_json=value)


FACTS = [
    make_fact("f-skill-1", "skill", {"name": "Python", "detail": "FastAPI、asyncio 异步开发"}),
    make_fact("f-skill-2", "skill", {"name": "数据库", "detail": "PostgreSQL、Redis 调优"}),
    make_fact(
        "f-work-1",
        "work_experience",
        {
            "company": "星辰科技有限公司",
            "start_raw": "2022.7",
            "end_raw": "至今",
            "is_current": True,
        },
    ),
    make_fact("f-edu-1", "education", {"school": "郑州大学", "degree": "本科"}),
]


def plan_ctx(profile, **overrides):
    defaults = dict(
        city_codes=["110100"],
        work_modes=["onsite", "hybrid"],
        minimum_monthly_salary=None,
        allow_outsourcing=False,
        blocked_company_ids=set(),
        blocked_company_names={},
        user_education_rank=profile.education_rank,
        user_education_level=profile.education_level,
        user_years_experience=profile.years_experience,
    )
    defaults.update(overrides)
    return PlanContext(**defaults)


def run_scoring(facts=FACTS, posting=None, plan=None, cosine=0.6, learned=None):
    posting = posting or make_posting()
    plan = plan or make_plan_obj()
    profile = build_user_profile(facts)
    hard = evaluate_hard_conditions(plan_ctx(profile), posting)
    return compute_score(
        profile=profile,
        plan=plan,
        posting=posting,
        hard_results=hard,
        cosine_similarity=cosine,
        company_preference=None,
        learned_weights=learned or {},
    )


def test_every_component_has_evidence_or_insufficient_evidence():
    """核心不变量：分项要么有证据引用，要么显式 insufficient_evidence。"""
    result = run_scoring()
    assert result.scoring_version == SCORING_VERSION
    assert {c.component for c in result.components} == set(COMPONENT_WEIGHTS)
    for comp in result.components:
        assert comp.weight == COMPONENT_WEIGHTS[comp.component]
        assert comp.evidence_refs or comp.uncertainty == INSUFFICIENT_EVIDENCE, comp.component
    # 匹配技能的证据必须引用 fact_id 且带岗位 span
    core = next(c for c in result.components if c.component == "core_skills")
    matched_refs = [e for e in core.evidence_refs if e.get("profile_fact_ids")]
    assert matched_refs, "应有引用 fact_id 的技能证据"
    for ref in matched_refs:
        assert ref["job_evidence"]["span"], "岗位证据 span 不得为空"


def test_insufficient_evidence_path_no_fabrication():
    """无任何事实时：分项显式 insufficient_evidence 而非编造分数。"""
    result = run_scoring(facts=[])
    by_name = {c.component: c for c in result.components}
    assert by_name["experience"].uncertainty == INSUFFICIENT_EVIDENCE
    assert by_name["experience"].score == 0
    assert by_name["project_evidence"].uncertainty == INSUFFICIENT_EVIDENCE
    core = by_name["core_skills"]
    # 岗位有技术词但无匹配：0 分且缺口列表来自岗位原文（不是编造用户能力）
    assert core.score == 0
    gap_refs = [e for e in core.evidence_refs if e.get("strength") == "gap"]
    assert gap_refs and gap_refs[0]["missing_terms"]


def test_grade_thresholds():
    assert grade_for(80) == "high"
    assert grade_for(79) == "potential"
    assert grade_for(65) == "potential"
    assert grade_for(64) == "low"


def test_total_score_is_weighted_sum_and_deterministic():
    r1 = run_scoring()
    r2 = run_scoring()
    assert r1.score_total == r2.score_total  # 确定性
    expected = round(sum(c.score * c.weight / 100 for c in r1.components))
    assert r1.score_total == expected


def test_learned_preferences_only_affect_preference_component():
    """学习偏好只动用户偏好分项（10 分），其余分项完全不变。"""
    base = run_scoring()
    # 岗位薪资低于目标 → not_interested:salary 学习偏好生效
    posting = make_posting(salary_min=15000, salary_max=20000, salary_raw="15-20K")
    without = run_scoring(posting=posting)
    with_learned = run_scoring(posting=posting, learned={"not_interested:salary": 4})
    by_name_a = {c.component: c for c in without.components}
    by_name_b = {c.component: c for c in with_learned.components}
    for name in COMPONENT_WEIGHTS:
        if name == "preference":
            assert by_name_b[name].score < by_name_a[name].score
        else:
            assert by_name_b[name].score == by_name_a[name].score
    assert base.score_total >= with_learned.score_total


def test_experience_penalty_reduces_component():
    """经验差 1 年内：硬条件放行但经验分项降分。"""
    posting = make_posting(experience_min=3, experience_max=5)
    facts = [
        make_fact("f-w", "work_experience",
                  {"company": "星辰科技有限公司", "start_raw": "2024.1", "end_raw": "至今",
                   "is_current": True}),
        make_fact("f-s", "skill", {"name": "Python", "detail": "FastAPI"}),
    ]
    result = run_scoring(facts=facts, posting=posting)
    exp = next(c for c in result.components if c.component == "experience")
    assert exp.score <= 50
    assert exp.evidence_refs[0]["profile_fact_ids"] == ["f-w"]


# ---------------- 受保护属性对偶测试（确定性） ----------------

RESUME_TEXT = """# 张三 — Python 后端工程师

## 个人概况
- 性别：男 | 年龄：27
- 手机：13812345678 | 邮箱：zhangsan@example.com

## 教育背景
- 2016.09 – 2020.06　郑州大学　软件工程　本科

## 专业技能
- Python：FastAPI、asyncio 异步开发
- 数据库：PostgreSQL、Redis 调优

## 工作经历
### 2022.07 – 至今　北京星辰科技有限公司　后端工程师
负责核心服务开发。
"""


def _facts_from_text(text):
    output = filter_protected(RuleBasedExtractor().extract(text))
    return [
        SimpleNamespace(id=f"f-{i}", fact_type=c.fact_type, value_json=c.value_json)
        for i, c in enumerate(output.candidates)
    ]


def test_counterfactual_gender_age_do_not_change_scores():
    """同一简历只改性别/年龄字段，总分与全部分项必须完全一致。"""
    variant = RESUME_TEXT.replace("性别：男 | 年龄：27", "性别：女 | 年龄：45")
    facts_a = _facts_from_text(RESUME_TEXT)
    facts_b = _facts_from_text(variant)
    result_a = run_scoring(facts=facts_a)
    result_b = run_scoring(facts=facts_b)
    assert result_a.score_total == result_b.score_total
    comp_a = {(c.component, c.score, c.gap_level, c.uncertainty) for c in result_a.components}
    comp_b = {(c.component, c.score, c.gap_level, c.uncertainty) for c in result_b.components}
    assert comp_a == comp_b
