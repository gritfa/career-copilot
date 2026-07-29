"""标准化器纯规则单元测试（docs/06 第 7 节各分支，无 DB/Redis）。"""

from app.jobs.adapters.boss import build_boss_search_url, build_plan_search_urls
from app.jobs.normalize import (
    detect_outsourcing_signals,
    normalize_city,
    normalize_education,
    normalize_employment_type,
    normalize_experience,
    normalize_role_family,
    normalize_salary,
    normalize_title,
)

# ---------------- 7.1 职位分类 ----------------


def test_role_family_maps_six_directions():
    assert normalize_role_family("大模型应用工程师")[0] == "ai_application"
    assert normalize_role_family("Python 后端开发工程师")[0] == "backend_python"
    assert normalize_role_family("Java 开发工程师")[0] == "backend_java"
    assert normalize_role_family("前端开发工程师（远程）")[0] == "frontend"
    assert normalize_role_family("数据分析师")[0] == "data"
    assert normalize_role_family("测试开发工程师")[0] == "qa"


def test_role_family_unknown_not_forced():
    """无法可靠分类 → unknown，不得强行分配（docs/06 7.1）。"""
    family, confidence = normalize_role_family("资深售前顾问")
    assert family == "unknown"
    assert confidence is None
    # python + java 同现且无更专门方向 → unknown
    assert normalize_role_family("Java/Python 开发工程师")[0] == "unknown"


def test_title_normalization_strips_brackets_and_spaces():
    assert normalize_title("Python 后端开发工程师（已下架）") == "python后端开发工程师"
    assert normalize_title("前端开发工程师（远程）") == "前端开发工程师"


# ---------------- 7.2 城市 ----------------


def test_city_six_cities_mapped_to_admin_codes():
    assert normalize_city("北京") == ("110100", "city")
    assert normalize_city("上海") == ("310100", "city")
    assert normalize_city("深圳") == ("440300", "city")
    assert normalize_city("杭州") == ("330100", "city")
    assert normalize_city("广州") == ("440100", "city")
    assert normalize_city("成都") == ("510100", "city")


def test_city_remote_nationwide_other_separated():
    assert normalize_city("远程") == (None, "remote")
    assert normalize_city("全国") == (None, "nationwide")
    # 六城之外可识别文本 → other，不硬塞行政区码
    assert normalize_city("武汉") == (None, "other")
    assert normalize_city(None) == (None, "unknown")
    assert normalize_city("  ") == (None, "unknown")


# ---------------- 7.3 薪资 ----------------


def test_salary_k_range_with_months():
    s = normalize_salary("20-35K·13薪")
    assert (s.salary_min, s.salary_max, s.months) == (20000, 35000, 13)
    assert s.unknown is False
    assert s.confidence == 1.0


def test_salary_negotiable_never_fabricated():
    """面议不得伪造数值（docs/06 7.3 硬约束）。"""
    s = normalize_salary("面议")
    assert s.unknown is True
    assert s.salary_min is None and s.salary_max is None
    assert s.confidence is None
    missing = normalize_salary(None)
    assert missing.unknown is True and missing.salary_min is None


def test_salary_daily_converted_with_confidence():
    s = normalize_salary("300-450元/天")
    assert s.unit == "daily"
    assert s.salary_min == int(300 * 21.75)
    assert s.salary_max == int(450 * 21.75)
    assert s.confidence is not None and s.confidence < 1.0
    assert s.raw == "300-450元/天"  # 保留原始表达


def test_salary_yearly_wan_converted():
    s = normalize_salary("20-35万/年")
    assert s.unit == "yearly"
    assert s.salary_min == int(200000 / 12)
    assert s.salary_max == int(350000 / 12)
    assert s.confidence is not None and s.confidence < 1.0


def test_salary_unparseable_marked_unknown():
    s = normalize_salary("具有竞争力的薪酬")
    assert s.unknown is True and s.salary_min is None


# ---------------- 7.4 经验和学历 ----------------


def test_experience_branches():
    assert normalize_experience("3-5年") == (3, 5, "range")
    assert normalize_experience("1年以上") == (1, None, "range")
    assert normalize_experience("应届") == (0, 0, "fresh_grad")
    assert normalize_experience("经验不限") == (None, None, "unrestricted")
    assert normalize_experience(None) == (None, None, "unknown")


def test_education_required_vs_preferred_vs_unknown():
    assert normalize_education("本科及以上") == ("bachelor", "required")
    assert normalize_education("大专及以上") == ("associate", "required")
    assert normalize_education("硕士及以上") == ("master", "required")
    assert normalize_education("本科优先") == ("bachelor", "preferred")
    assert normalize_education("学历不限") == (None, "unknown")
    assert normalize_education(None) == (None, "unknown")


# ---------------- 7.5 用工与外包 ----------------


def test_employment_type_full_time_filtering():
    assert normalize_employment_type("全职") == "full_time"
    assert normalize_employment_type("实习") == "other"
    assert normalize_employment_type("兼职") == "other"
    assert normalize_employment_type(None) == "unknown"


def test_outsourcing_signals_with_evidence_and_confidence():
    signals = detect_outsourcing_signals(
        "驻场测试工程师（外包项目）", "人力外包项目，需长期驻场客户现场工作，接受派遣。"
    )
    assert signals, "外包/派遣/驻场关键词必须产生风险信号"
    for signal in signals:
        assert signal["code"] == "POSSIBLE_OUTSOURCING"
        assert signal["evidence"]  # 必须带证据片段
        assert 0 < signal["confidence"] <= 1.0
    # 标题命中的置信度更高
    title_hits = [s for s in signals if s["evidence"].startswith("title:")]
    assert any(s["confidence"] == 0.9 for s in title_hits)


def test_no_outsourcing_signal_for_clean_jd():
    assert detect_outsourcing_signals("Python 后端开发工程师", "负责核心服务开发") == []


# ---------------- BOSS link-out 纯函数 ----------------


def test_boss_search_url_pure_function():
    url = build_boss_search_url("backend_python", "110100")
    assert url.startswith("https://www.zhipin.com/web/geek/job?")
    assert "city=101010100" in url
    assert "query=" in url


def test_boss_plan_urls_cover_each_city():
    urls = build_plan_search_urls("frontend", ["110100", "310100"])
    assert len(urls) == 2
    assert {u["city_code"] for u in urls} == {"110100", "310100"}
    assert all(u["source_key"] == "boss" for u in urls)
