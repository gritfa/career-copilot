"""岗位标准化器（docs/06 第 7 节，规则版）。

原则：
- 无法可靠分类的 role_family 标 unknown，不得强行分配。
- 面议/缺失薪资绝不伪造数值（salary_unknown=True 且 min/max 为空）。
- 日薪/年薪换算保留原始表达与换算置信度。
- 外包/派遣/驻场输出证据片段与置信度，不把不确定判断宣传为事实。
"""

import re
from dataclasses import dataclass

from app.jobs.constants import CITY_CODE_BY_NAME, ROLE_FAMILY_UNKNOWN

NORMALIZER_VERSION = "1"

# 平均月工作日（人社部口径 21.75），用于日薪换算
_DAYS_PER_MONTH = 21.75


# ---------------- 职位分类（7.1） ----------------

# 按特异性排序：先匹配更专门的方向，python/java 兜底
_ROLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ai_application", ("大模型", "llm", "aigc", "ai应用", "多模态", "提示工程", "prompt")),
    ("qa", ("测试", "qa", "sdet", "质量保障")),
    ("data", ("数据分析", "数据开发", "数据工程", "数仓", "大数据", "etl", " bi ", "数据平台")),
    ("frontend", ("前端", "web开发", "react", "vue", "小程序开发")),
    ("backend_java", ("java",)),
    ("backend_python", ("python",)),
)


def normalize_role_family(title: str, description: str = "") -> tuple[str, float | None]:
    """标题（辅以正文）→ 六方向之一或 unknown；返回 (family, confidence)。"""
    title_l = f" {title.lower()} "
    for family, keywords in _ROLE_RULES:
        if any(kw in title_l for kw in keywords):
            # python/java 同现且无更专门方向 → 无法可靠分类
            if family in ("backend_java", "backend_python") and (
                "java" in title_l and "python" in title_l
            ):
                return ROLE_FAMILY_UNKNOWN, None
            return family, 0.9
    desc_l = description.lower()
    for family, keywords in _ROLE_RULES:
        if any(kw in desc_l for kw in keywords):
            return family, 0.5  # 仅正文命中：低置信
    return ROLE_FAMILY_UNKNOWN, None


_TITLE_NOISE_RE = re.compile(r"[（(][^（()）]*[)）]")
_SPACE_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """去括号备注/空白并小写化，用作去重键（原始标题另存 title_raw）。"""
    cleaned = _TITLE_NOISE_RE.sub("", title)
    return _SPACE_RE.sub("", cleaned).lower()[:255]


# ---------------- 城市（7.2） ----------------


def normalize_city(text: str | None) -> tuple[str | None, str]:
    """城市文本 → (行政区码或 None, city_kind)。

    kind: city（六城之一）/ remote / nationwide / other（可识别但非六城）/ unknown。
    """
    if not text or not text.strip():
        return None, "unknown"
    value = text.strip()
    if "远程" in value or value.lower() == "remote":
        return None, "remote"
    if "全国" in value or "多个城市" in value:
        return None, "nationwide"
    for name, code in CITY_CODE_BY_NAME.items():
        if name in value:
            return code, "city"
    return None, "other"


# ---------------- 薪资（7.3） ----------------


@dataclass(frozen=True)
class SalaryNorm:
    """CNY 月薪标准化结果；unknown=True 时 min/max 必为空（不伪造）。"""

    salary_min: int | None
    salary_max: int | None
    months: int | None
    unknown: bool
    raw: str | None
    confidence: float | None
    unit: str | None  # monthly / daily / yearly / None


_MONTHS_RE = re.compile(r"[·\s]*(\d{2})\s*薪")
_RANGE_K_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~—]\s*(\d+(?:\.\d+)?)\s*[kK千]")
_RANGE_WAN_YEAR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~—]\s*(\d+(?:\.\d+)?)\s*万\s*/\s*年")
_RANGE_DAY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~—]\s*(\d+(?:\.\d+)?)\s*(?:元)?\s*/\s*天")
_RANGE_YUAN_MONTH_RE = re.compile(
    r"(\d{4,6})\s*[-~—]\s*(\d{4,6})\s*(?:元)?\s*(?:/\s*月)?(?![kK千万/\d])"
)


def normalize_salary(text: str | None) -> SalaryNorm:
    """薪资文本 → CNY 月薪 min/max + 薪数；面议/缺失 → unknown 三态之一。"""
    raw = (text or "").strip() or None
    if raw is None or "面议" in raw or raw in ("薪资面议", "待遇面议"):
        return SalaryNorm(None, None, None, True, raw, None, None)

    months: int | None = None
    m = _MONTHS_RE.search(raw)
    if m:
        candidate = int(m.group(1))
        if 12 <= candidate <= 18:
            months = candidate

    if m_k := _RANGE_K_RE.search(raw):
        lo, hi = float(m_k.group(1)) * 1000, float(m_k.group(2)) * 1000
        return SalaryNorm(int(lo), int(hi), months, False, raw, 1.0, "monthly")

    if m_y := _RANGE_WAN_YEAR_RE.search(raw):
        lo = float(m_y.group(1)) * 10000 / 12
        hi = float(m_y.group(2)) * 10000 / 12
        # 年薪换月薪：不知道薪数结构，置信度降档
        return SalaryNorm(int(lo), int(hi), months, False, raw, 0.7, "yearly")

    if m_d := _RANGE_DAY_RE.search(raw):
        lo = float(m_d.group(1)) * _DAYS_PER_MONTH
        hi = float(m_d.group(2)) * _DAYS_PER_MONTH
        return SalaryNorm(int(lo), int(hi), months, False, raw, 0.6, "daily")

    if m_m := _RANGE_YUAN_MONTH_RE.search(raw):
        lo, hi = int(m_m.group(1)), int(m_m.group(2))
        return SalaryNorm(lo, hi, months, False, raw, 0.9, "monthly")

    # 无法解析：按不确定处理，不猜数值
    return SalaryNorm(None, None, months, True, raw, None, None)


# ---------------- 经验与学历（7.4） ----------------

_EXP_RANGE_RE = re.compile(r"(\d+)\s*[-~—]\s*(\d+)\s*年")
_EXP_MIN_RE = re.compile(r"(\d+)\s*年(?:以上|及以上|\+)")


def normalize_experience(text: str | None) -> tuple[int | None, int | None, str]:
    """经验文本 → (min, max, type)；type ∈ range/fresh_grad/unrestricted/unknown。"""
    if not text or not text.strip():
        return None, None, "unknown"
    value = text.strip()
    if "应届" in value:
        return 0, 0, "fresh_grad"
    if "不限" in value:
        return None, None, "unrestricted"
    if m := _EXP_RANGE_RE.search(value):
        return int(m.group(1)), int(m.group(2)), "range"
    if m := _EXP_MIN_RE.search(value):
        return int(m.group(1)), None, "range"
    return None, None, "unknown"


_EDU_LEVELS: tuple[tuple[str, str], ...] = (
    ("博士", "phd"),
    ("硕士", "master"),
    ("研究生", "master"),
    ("本科", "bachelor"),
    ("大专", "associate"),
)


def normalize_education(text: str | None) -> tuple[str | None, str]:
    """学历文本 → (level, requirement_type)；required / preferred / unknown。"""
    if not text or not text.strip():
        return None, "unknown"
    value = text.strip()
    level = next((code for kw, code in _EDU_LEVELS if kw in value), None)
    if level is None:
        # “学历不限”等：无法归为硬性/优先要求
        return None, "unknown"
    if "优先" in value:
        return level, "preferred"
    # 明确写出学历且非“优先” → 视为硬性要求（“本科及以上”/“本科”）
    return level, "required"


# ---------------- 用工与外包（7.5） ----------------


def normalize_employment_type(text: str | None) -> str:
    """全职/其他/未知；只有全职岗位进入推荐池（外包另走风险信号）。"""
    if not text or not text.strip():
        return "unknown"
    value = text.strip()
    if any(kw in value for kw in ("实习", "兼职", "劳务", "临时")):
        return "other"
    if "全职" in value:
        return "full_time"
    return "unknown"


@dataclass(frozen=True)
class OutsourcingSignal:
    code: str
    keyword: str
    evidence: str
    confidence: float

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "keyword": self.keyword,
            "evidence": self.evidence,
            "confidence": self.confidence,
        }


_OUTSOURCING_KEYWORDS = ("人力外包", "外包", "派遣", "驻场", "外派")


def _snippet(text: str, index: int, width: int = 30) -> str:
    start = max(0, index - width)
    end = min(len(text), index + width)
    return text[start:end].strip()


def detect_outsourcing_signals(title: str, description: str) -> list[dict]:
    """规则版外包/派遣/驻场信号：输出证据片段与置信度（标题命中置信更高）。"""
    signals: list[OutsourcingSignal] = []
    seen: set[str] = set()
    for source_name, text, confidence in (
        ("title", title or "", 0.9),
        ("description", description or "", 0.7),
    ):
        for keyword in _OUTSOURCING_KEYWORDS:
            idx = text.find(keyword)
            if idx < 0:
                continue
            # “人力外包”命中后不再重复记“外包”
            if any(keyword in s for s in seen):
                continue
            seen.add(keyword)
            signals.append(
                OutsourcingSignal(
                    code="POSSIBLE_OUTSOURCING",
                    keyword=keyword,
                    evidence=f"{source_name}: …{_snippet(text, idx)}…",
                    confidence=confidence,
                )
            )
    return [s.as_dict() for s in signals]
