"""确定性规则解析器：正则 + 启发式抽取候选事实。

铁律（docs/08 第 11 节、agent-prompts/00）：
- 受保护属性（性别/年龄/出生日期/照片/婚育/民族/宗教/籍贯）即使解析到
  也不生成候选：直接丢弃并只记 **计数**，不记录内容。
- 每个候选带 source_span（字符偏移）与 confidence，便于前端定位证据。
- 不虚构：所有候选都必须有真实文本 span 支撑。
"""

import hashlib
import re
from collections.abc import Iterator
from typing import Any

from app.integrations.llm_structured import CandidateDraft, ExtractionOutput

PARSER_NAME = "careercopilot-rules"
PARSER_VERSION = "1.0.0"

# ---- 受保护属性：只计数，不产出候选 ----
PROTECTED_FACT_TYPES = frozenset(
    {"gender", "age", "birth_date", "photo", "marital_status", "ethnicity", "native_place"}
)

_PROTECTED_PATTERNS = [
    re.compile(r"性\s*别\s*[:：]?\s*(男|女)"),
    re.compile(r"年\s*龄\s*[:：]?\s*\d{1,3}"),
    re.compile(r"出生(?:年月|日期)"),
    re.compile(r"民\s*族\s*[:：]"),
    re.compile(r"籍\s*贯\s*[:：]"),
    re.compile(r"(?:婚姻|婚育)状?况?|已婚|未婚"),
    re.compile(r"宗\s*教"),
    re.compile(r"(?:证件)?照片"),
]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 完整或打码的大陆手机号（139****5310）
_PHONE_RE = re.compile(r"1[3-9]\d(?:\d{8}|\*{3,4}\d{4})")
_PERIOD_RE = re.compile(
    r"((?:19|20)\d{2})\s*[./年]\s*(\d{1,2})\s*[月]?\s*[–—~\-至]+\s*"
    r"(至今|现在|(?:19|20)\d{2}\s*[./年]\s*\d{1,2})"
)
_DEGREE_RE = re.compile(r"(博士|硕士|本科|学士|大专|专科)")
_SCHOOL_RE = re.compile(r"([一-鿿]{2,16}(?:大学|学院))")
_COMPANY_RE = re.compile(
    r"([一-鿿]{2,20}(?:公司|集团|银行|研究院|事务所|工作室))"
)
_BULLET_RE = re.compile(r"^\s*[-•·*]\s*")
_HEADING_RE = re.compile(r"^\s*#{0,6}\s*[一-鿿]{2,10}\s*$")
_SKILL_HEADING_RE = re.compile(r"^\s*#{0,6}\s*(?:专业)?技能")


def _hash_quote(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


def _lines_with_offsets(text: str) -> Iterator[tuple[int, str]]:
    for match in re.finditer(r"[^\n]+", text):
        yield match.start(), match.group()


class RuleBasedExtractor:
    """正则 + 启发式的确定性抽取器（实现 StructuredExtractionAdapter 协议）。"""

    parser_name = PARSER_NAME
    parser_version = PARSER_VERSION
    model_provider: str | None = None
    model_version: str | None = None

    def extract(self, text: str) -> ExtractionOutput:
        output = ExtractionOutput()
        output.protected_discarded_count = self._count_protected(text)

        drafts: list[CandidateDraft] = []
        drafts.extend(self._extract_regex(text, _EMAIL_RE, "contact_email", "email", 0.95))
        drafts.extend(self._extract_regex(text, _PHONE_RE, "contact_phone", "phone", 0.9))
        drafts.extend(self._extract_education(text))
        drafts.extend(self._extract_work_periods(text))
        drafts.extend(self._extract_skills(text))

        output.candidates = self._dedupe(drafts)
        return output

    # ---- 受保护属性：只数命中次数 ----

    def _count_protected(self, text: str) -> int:
        return sum(len(p.findall(text)) for p in _PROTECTED_PATTERNS)

    # ---- 通用正则抽取 ----

    def _extract_regex(
        self, text: str, pattern: re.Pattern[str], fact_type: str, key: str, confidence: float
    ) -> list[CandidateDraft]:
        drafts = []
        for match in pattern.finditer(text):
            quote = match.group()
            drafts.append(
                CandidateDraft(
                    fact_type=fact_type,
                    value_json={key: quote},
                    span_start=match.start(),
                    span_end=match.end(),
                    quote=quote,
                    confidence=confidence,
                )
            )
        return drafts

    # ---- 教育经历：同一行出现 学校 + 学历 ----

    def _extract_education(self, text: str) -> list[CandidateDraft]:
        drafts = []
        for offset, line in _lines_with_offsets(text):
            school = _SCHOOL_RE.search(line)
            degree = _DEGREE_RE.search(line)
            if not (school and degree):
                continue
            period = _PERIOD_RE.search(line)
            value: dict[str, Any] = {
                "school": school.group(1),
                "degree": degree.group(1),
            }
            if period:
                value["period_raw"] = period.group().strip()
            drafts.append(
                CandidateDraft(
                    fact_type="education",
                    value_json=value,
                    span_start=offset,
                    span_end=offset + len(line),
                    quote=line.strip(),
                    confidence=0.8,
                )
            )
        return drafts

    # ---- 工作时间段：时间段 + 公司特征词（排除教育行） ----

    def _extract_work_periods(self, text: str) -> list[CandidateDraft]:
        drafts = []
        for offset, line in _lines_with_offsets(text):
            period = _PERIOD_RE.search(line)
            if not period:
                continue
            if _SCHOOL_RE.search(line) and _DEGREE_RE.search(line):
                continue  # 教育行由 _extract_education 处理
            company = _COMPANY_RE.search(line)
            if not company:
                continue
            end_raw = period.group(3).strip()
            value = {
                "company": company.group(1),
                "start_raw": f"{period.group(1)}.{period.group(2)}",
                "end_raw": end_raw,
                "is_current": end_raw in ("至今", "现在"),
            }
            drafts.append(
                CandidateDraft(
                    fact_type="work_experience",
                    value_json=value,
                    span_start=offset,
                    span_end=offset + len(line),
                    quote=line.strip(),
                    confidence=0.75,
                )
            )
        return drafts

    # ---- 技能：技能标题后的连续条目 ----

    def _extract_skills(self, text: str) -> list[CandidateDraft]:
        drafts: list[CandidateDraft] = []
        in_skill_section = False
        for offset, line in _lines_with_offsets(text):
            if _SKILL_HEADING_RE.match(line):
                in_skill_section = True
                continue
            if not in_skill_section:
                continue
            if _BULLET_RE.match(line):
                content = _BULLET_RE.sub("", line).strip()
                if not content:
                    continue
                label, _, detail = content.partition("：")
                value: dict[str, Any] = {"name": label.strip()[:64]}
                if detail:
                    value["detail"] = detail.strip()[:200]
                drafts.append(
                    CandidateDraft(
                        fact_type="skill",
                        value_json=value,
                        span_start=offset,
                        span_end=offset + len(line),
                        quote=line.strip(),
                        confidence=0.7,
                    )
                )
            elif _HEADING_RE.match(line) or line.strip().startswith("#"):
                in_skill_section = False  # 到下一个章节标题为止
        return drafts

    # ---- 去重：同类型同值只保留首个 ----

    def _dedupe(self, drafts: list[CandidateDraft]) -> list[CandidateDraft]:
        seen: set[tuple[str, str]] = set()
        result = []
        for draft in drafts:
            key = (draft.fact_type, repr(sorted(draft.value_json.items())))
            if key in seen:
                continue
            seen.add(key)
            result.append(draft)
        return result


def filter_protected(output: ExtractionOutput) -> ExtractionOutput:
    """防御性二次过滤：任何抽取实现产出受保护属性类型都在此丢弃并计数。"""
    kept = [c for c in output.candidates if c.fact_type not in PROTECTED_FACT_TYPES]
    discarded = len(output.candidates) - len(kept)
    return ExtractionOutput(
        candidates=kept,
        protected_discarded_count=output.protected_discarded_count + discarded,
    )


def quote_hash(quote: str) -> str:
    """候选证据引文哈希（sha256）。"""
    return _hash_quote(quote)
