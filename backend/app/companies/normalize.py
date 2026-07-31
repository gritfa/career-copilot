"""公司名标准化规则 v1（阶段 10 任务 D）。

原则（负责人任务书硬边界）：
- 只做**确定性**规范化：空白 / 全半角 / 中英文括号 / 大小写 / 常见法律与行业后缀。
- ``normalized`` 相等 = 同一写法的变体（高置信）；``core`` 相等只是**候选证据**，
  必须进 ``company_alias_reviews`` 人工审核，绝不凭字符串相似自动合并公司。
- 垃圾 / 占位名返回 ``None``，不允许污染公司主数据。
- 规则版本号随行落库（``rules_version``），人工修正可追溯到当时的规则。
"""

import re
import unicodedata
from dataclasses import dataclass

COMPANY_NAME_RULES_VERSION = "company_norm_v1"

# 占位 / 无信息名：一律拒绝入库（在 normalized 形态上比较）
_PLACEHOLDER_NAMES = frozenset(
    {
        "-",
        "--",
        "/",
        ".",
        "n/a",
        "na",
        "null",
        "none",
        "unknown",
        "无",
        "未知",
        "暂无",
        "保密",
        "公司保密",
        "某公司",
        "某某公司",
    }
)

_WS_RE = re.compile(r"\s+")
# 中文/其他宽括号统一为半角圆括号（NFKC 已处理全角（），这里兜底其他变体）
_BRACKET_MAP = str.maketrans({"（": "(", "）": ")", "【": "(", "】": ")", "〔": "(", "〕": ")"})
_BRACKETED_RE = re.compile(r"\([^()]*\)")

# 法律形态后缀：从尾部迭代剥离（core 专用）
_LEGAL_SUFFIXES: tuple[str, ...] = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "股份公司",
    "合伙企业",
    "有限合伙",
    "分公司",
    "公司",
    "集团",
    "控股",
    "股份",
)

# 行业通用尾词：只在剥离法律后缀之后再剥（core 专用）
_INDUSTRY_SUFFIXES: tuple[str, ...] = (
    "信息技术",
    "网络科技",
    "科技发展",
    "电子商务",
    "互联网",
    "科技",
    "技术",
    "网络",
    "信息",
    "软件",
    "通讯",
    "通信",
)

# 常见地区前缀（core 专用；仅用于生成低置信候选键，绝不据此自动合并）
_REGION_PREFIXES: tuple[str, ...] = (
    "北京市",
    "上海市",
    "深圳市",
    "杭州市",
    "广州市",
    "成都市",
    "北京",
    "上海",
    "深圳",
    "杭州",
    "广州",
    "成都",
    "天津",
    "重庆",
    "南京",
    "武汉",
    "西安",
    "苏州",
    "合肥",
    "厦门",
    "珠海",
    "东莞",
    "佛山",
    "青岛",
    "大连",
    "长沙",
    "郑州",
    "福州",
    "济南",
    "宁波",
    "无锡",
    "中国",
    "广东",
    "浙江",
    "江苏",
    "四川",
    "山东",
    "福建",
    "湖北",
    "湖南",
    "安徽",
    "河南",
)


@dataclass(frozen=True)
class CompanyNameNorm:
    """标准化结果：display 用于展示，normalized 高置信键，core 低置信候选键。"""

    raw: str
    display: str
    normalized: str
    core: str
    applied_rules: tuple[str, ...]
    rules_version: str


def _strip_iter(value: str, suffixes: tuple[str, ...], rules: list[str], tag: str) -> str:
    """从尾部迭代剥离后缀；剥空则保留剥离前的最后一个非空值。"""
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if value.endswith(suffix) and len(value) > len(suffix):
                value = value[: -len(suffix)]
                rules.append(f"{tag}:{suffix}")
                changed = True
                break
    return value


def normalize_company_name(raw: str | None) -> CompanyNameNorm | None:
    """公司原始名 → 标准化结果；垃圾 / 占位 / 空名返回 None。"""
    if raw is None:
        return None
    display = _WS_RE.sub(" ", raw).strip()
    if not display:
        return None

    rules: list[str] = []

    # normalized：NFKC（全角→半角、全角空格等）+ 括号统一 + 去空白 + 小写
    normalized = unicodedata.normalize("NFKC", display)
    if normalized != display:
        rules.append("nfkc")
    bracketed = normalized.translate(_BRACKET_MAP)
    if bracketed != normalized:
        rules.append("brackets")
    normalized = _WS_RE.sub("", bracketed)
    lowered = normalized.lower()
    if lowered != normalized:
        rules.append("lowercase")
    normalized = lowered

    if not normalized or normalized in _PLACEHOLDER_NAMES:
        return None
    # 必须含至少一个字母/数字/汉字，纯符号名拒绝
    if not any(ch.isalnum() for ch in normalized):
        return None

    # core：去括号段 → 去地区前缀 → 迭代剥法律后缀 → 剥行业尾词
    core = _BRACKETED_RE.sub("", normalized)
    if core != normalized:
        rules.append("drop_bracketed")
    for prefix in _REGION_PREFIXES:
        if core.startswith(prefix) and len(core) > len(prefix):
            core = core[len(prefix) :]
            rules.append(f"region:{prefix}")
            break
    core = _strip_iter(core, _LEGAL_SUFFIXES, rules, "legal")
    core = _strip_iter(core, _INDUSTRY_SUFFIXES, rules, "industry")
    # 候选键太短没有区分度：退回 normalized，避免离谱候选
    if len(core) < 2:
        core = normalized

    return CompanyNameNorm(
        raw=raw,
        display=display,
        normalized=normalized[:255],
        core=core[:255],
        applied_rules=tuple(rules),
        rules_version=COMPANY_NAME_RULES_VERSION,
    )
