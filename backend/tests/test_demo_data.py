"""合成演示数据（app/demo/data.py）离线校验：不依赖 DB/Redis。

红线（docs/15 P1）：种子内容必须全部合成、可被标准化管道正确归类；
合成简历必须能被规则解析器抽出 教育/工作经历/技能 候选事实，且无受保护属性。
"""

from collections import Counter

from app.demo.data import (
    DEMO_RESUME_LINES,
    SEED_CITY_NAMES,
    build_demo_resume_docx,
    build_seed_job_payloads,
    expected_role_family,
)
from app.jobs.normalize import (
    normalize_city,
    normalize_employment_type,
    normalize_role_family,
    normalize_salary,
    normalize_title,
)
from app.resumes.extract import extract_text
from app.resumes.parser import RuleBasedExtractor

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_seed_jobs_shape_and_coverage():
    """25-30 个岗位、3 城市 × 4 方向全覆盖、source_job_id 稳定唯一。"""
    items = build_seed_job_payloads()
    assert 25 <= len(items) <= 30

    ids = [sjid for sjid, _ in items]
    assert len(set(ids)) == len(ids)

    cities = {p["city"] for _, p in items}
    assert cities == set(SEED_CITY_NAMES) and len(cities) == 3

    families = Counter(expected_role_family(sjid) for sjid, _ in items)
    assert set(families) == {"backend_python", "ai_application", "data", "frontend"}
    for family, count in families.items():
        assert count >= 3, f"方向 {family} 岗位过少：{count}"


def test_seed_jobs_normalize_cleanly():
    """每个种子岗位都能被标准化管道正确归类（方向/城市/薪资/全职）。"""
    for sjid, payload in build_seed_job_payloads():
        family, _ = normalize_role_family(payload["title"], payload["description"])
        assert family == expected_role_family(sjid), (sjid, payload["title"], family)

        _, city_kind = normalize_city(payload["city"])
        assert city_kind == "city", (sjid, payload["city"])

        salary = normalize_salary(payload["salary"])
        assert not salary.unknown and salary.salary_min and salary.salary_max, (sjid, salary)

        assert normalize_employment_type(payload["employment"]) == "full_time"


def test_seed_jobs_marked_synthetic_and_no_dedupe_collision():
    """正文自带合成声明；（公司+标准职位+城市）唯一，不触发跨岗位合并。"""
    seen: set[tuple[str, str, str]] = set()
    for sjid, payload in build_seed_job_payloads():
        assert "合成示例数据" in payload["description"], sjid
        key = (payload["company"], normalize_title(payload["title"]), payload["city"])
        assert key not in seen, ("去重键冲突（会被合并成同一 canonical）", key, sjid)
        seen.add(key)


def test_demo_resume_parses_to_expected_facts():
    """合成简历 → 规则解析器：教育 1 + 工作经历 2 + 技能若干，且零受保护属性。"""
    text = extract_text(build_demo_resume_docx(), DOCX_MIME)
    output = RuleBasedExtractor().extract(text)

    by_type = Counter(c.fact_type for c in output.candidates)
    assert by_type["education"] == 1
    assert by_type["work_experience"] == 2
    assert by_type["skill"] >= 4
    assert output.protected_discarded_count == 0

    # 内容红线：明确标注合成人物
    assert any("合成示例人物" in line for line in DEMO_RESUME_LINES)
