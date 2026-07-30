"""公司名标准化规则 v1 单元测试（离线，不需要 DB）。"""

import pytest

from app.companies.aliases import load_alias_config
from app.companies.normalize import (
    COMPANY_NAME_RULES_VERSION,
    normalize_company_name,
)

# ---------------- normalized（高置信键） ----------------


def test_whitespace_and_case_variants_share_normalized_key():
    a = normalize_company_name("星岚科技")
    b = normalize_company_name("  星岚 科技 ")
    assert a is not None and b is not None
    assert a.normalized == b.normalized == "星岚科技"


def test_fullwidth_and_bracket_variants_share_normalized_key():
    a = normalize_company_name("腾讯科技（深圳）有限公司")
    b = normalize_company_name("腾讯科技(深圳)有限公司")
    c = normalize_company_name("腾讯科技【深圳】有限公司")
    assert a is not None and b is not None and c is not None
    assert a.normalized == b.normalized == c.normalized


def test_latin_case_insensitive():
    a = normalize_company_name("ByteDance")
    b = normalize_company_name("bytedance")
    c = normalize_company_name("ＢｙｔｅＤａｎｃｅ")  # 全角字母 → NFKC 半角
    assert a is not None and b is not None and c is not None
    assert a.normalized == b.normalized == c.normalized == "bytedance"


# ---------------- core（低置信候选键） ----------------


def test_core_strips_legal_and_industry_suffixes():
    norm = normalize_company_name("北京字节跳动科技有限公司")
    assert norm is not None
    assert norm.core == "字节跳动"
    assert any(r.startswith("legal:") for r in norm.applied_rules)
    assert any(r.startswith("region:") for r in norm.applied_rules)


def test_core_strips_bracketed_region():
    norm = normalize_company_name("腾讯科技（深圳）有限公司")
    assert norm is not None
    assert norm.core == "腾讯"


def test_core_falls_back_to_normalized_when_too_short():
    # 剥完后缀只剩单字：无区分度，退回 normalized，避免离谱候选
    norm = normalize_company_name("云科技有限公司")
    assert norm is not None
    assert norm.core == norm.normalized


def test_rules_version_stamped():
    norm = normalize_company_name("华为技术有限公司")
    assert norm is not None
    assert norm.rules_version == COMPANY_NAME_RULES_VERSION
    assert norm.core == "华为"


# ---------------- 垃圾 / 占位名拒绝 ----------------


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "-", "/", "N/A", "n/a", "无", "未知", "保密", "某公司", "###", "！！"],
)
def test_placeholder_and_garbage_names_rejected(raw):
    assert normalize_company_name(raw) is None


def test_display_keeps_original_form():
    norm = normalize_company_name("  Ant  Group ")
    assert norm is not None
    assert norm.display == "Ant Group"
    assert norm.normalized == "antgroup"


# ---------------- 别名配置 ----------------


def test_alias_config_loads_and_maps_known_aliases():
    config = load_alias_config()
    assert config.version == 1
    assert config.rules_version == COMPANY_NAME_RULES_VERSION
    for alias, canonical in [
        ("bytedance", "字节跳动"),
        ("北京字节跳动科技有限公司", "字节跳动"),
        ("腾讯科技(深圳)有限公司", "腾讯"),
        ("antgroup", "蚂蚁集团"),
    ]:
        assert config.alias_to_canonical.get(alias) == canonical
    # canonical 自身也可反查
    assert config.alias_to_canonical.get("字节跳动") == "字节跳动"
