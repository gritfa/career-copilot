"""向量文本构造（docs/07 第 3 节 + 第 6 节公平性）。

- 简历侧 = 已确认事实 + 方案基础信息。**绝不包含**：姓名、电话、邮箱、
  性别、年龄、照片、婚育、民族、宗教、籍贯等受保护属性（有确定性测试）。
- 岗位侧 = 标准标题 + 职责 + 要求（description_text）。
- PREPROCESS_VERSION 变更即视为新向量版本，与旧向量隔离。
"""

import hashlib
import re

from app.jobs.constants import CITY_NAME_BY_CODE, ROLE_FAMILY_LABELS
from app.resumes.parser import PROTECTED_FACT_TYPES

PREPROCESS_VERSION = "p1"

# 联系方式事实类型：与受保护属性一样禁止进入向量文本
CONTACT_FACT_TYPES = frozenset({"contact_email", "contact_phone"})
EXCLUDED_FACT_TYPES = frozenset(PROTECTED_FACT_TYPES) | CONTACT_FACT_TYPES

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"1[3-9]\d(?:[\s-]?\d){8}|1[3-9]\d\*{3,4}\d{4}")


def scrub_contact(text: str) -> str:
    """防御性清洗：任何滑入的邮箱/手机号一律移除（双保险，不依赖上游过滤）。"""
    text = _EMAIL_RE.sub(" ", text)
    return _PHONE_RE.sub(" ", text)


def build_profile_vector_text(
    *,
    role_family: str,
    city_codes: list[str],
    work_modes: list[str],
    facts: list,
) -> str:
    """简历侧向量文本：方案方向/城市/办公方式 + 已确认技能/经历/教育。

    facts: 已确认 ProfileFact（含 fact_type/value_json）。联系方式与受保护
    属性类型直接跳过；输出再经 scrub_contact 双保险清洗。
    """
    parts: list[str] = [
        f"求职方向 {ROLE_FAMILY_LABELS.get(role_family, role_family)}",
        "城市 " + " ".join(CITY_NAME_BY_CODE.get(c, c) for c in city_codes),
        "办公方式 " + " ".join(work_modes),
    ]
    for fact in facts:
        if fact.fact_type in EXCLUDED_FACT_TYPES:
            continue
        value = fact.value_json or {}
        if fact.fact_type == "skill":
            text = " ".join(str(value.get(k, "")) for k in ("name", "detail")).strip()
            if text:
                parts.append(f"技能 {text}")
        elif fact.fact_type == "work_experience":
            company = str(value.get("company", "")).strip()
            if company:
                parts.append(f"工作经历 {company}")
        elif fact.fact_type == "education":
            degree = str(value.get("degree", "")).strip()
            school = str(value.get("school", "")).strip()
            if degree or school:
                parts.append(f"教育 {school} {degree}".strip())
        # 其他未知事实类型：不进向量文本（白名单策略，宁缺毋滥）
    return scrub_contact("\n".join(parts))


def build_job_vector_text(posting) -> str:
    """岗位侧向量文本：标准标题 + 职责/要求正文。"""
    title = posting.title_raw or posting.title_normalized or ""
    description = posting.description_text or ""
    return f"{title}\n{description}".strip()


def text_hash(text: str) -> str:
    """向量文本哈希（入库用，文本本身不入库、不进日志）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
