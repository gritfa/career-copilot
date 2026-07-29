"""岗位采集/导入管道：snapshot → normalize → posting → dedupe → canonical。

- 原始内容进对象存储（key 只含来源 key + UUID），DB 存 key + content_hash，
  job_snapshots 行由 DB 触发器保证不可变。
- posting 是标准化派生层（可更新）；来源更新生成新快照行，不覆盖历史证据。
- 只有全职岗位进入 canonical 池；非全职保留 posting 证据但不并池。
- 同步 SQLAlchemy 实现：Celery 任务直接调用，API 导入经 asyncio.to_thread。
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Company, JobPosting, JobSnapshot, JobSource
from app.integrations.storage import get_storage
from app.jobs.adapters.base import RawJobSnapshot
from app.jobs.dedupe import assign_canonical
from app.jobs.normalize import (
    NORMALIZER_VERSION,
    detect_outsourcing_signals,
    normalize_city,
    normalize_education,
    normalize_employment_type,
    normalize_experience,
    normalize_role_family,
    normalize_salary,
    normalize_title,
)

logger = structlog.get_logger("app.jobs.pipeline")

SNAPSHOT_PARSER_VERSION = "1"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class IngestResult:
    posting_id: uuid.UUID
    canonical_job_id: uuid.UUID | None
    dedupe_status: str
    created: bool
    changed: bool


def get_or_create_company(db: Session, name: str | None) -> Company | None:
    """按规范名 get-or-create 公司（名称非正文/非个人信息，可入库）。"""
    if not name or not name.strip():
        return None
    canonical = name.strip()
    company = db.execute(
        select(Company).where(Company.canonical_name == canonical)
    ).scalar_one_or_none()
    if company is None:
        company = Company(id=uuid.uuid4(), canonical_name=canonical, aliases=[])
        db.add(company)
        db.flush()
    return company


def create_snapshot(db: Session, source: JobSource, raw: RawJobSnapshot) -> JobSnapshot:
    """保存不可变快照：原始内容进存储，DB 只存 key + 哈希 + HTTP 元数据。"""
    content_hash = hashlib.sha256(raw.content).hexdigest()
    storage_key = f"job_snapshots/{source.source_key}/{uuid.uuid4().hex}.raw"
    get_storage().save(storage_key, raw.content)
    snapshot = JobSnapshot(
        id=uuid.uuid4(),
        job_source_id=source.id,
        source_url=raw.ref.url or None,
        fetched_at=raw.fetched_at,
        content_hash=content_hash,
        storage_key=storage_key,
        http_meta_json={**raw.http_meta, "media_type": raw.media_type},
        parser_version=SNAPSHOT_PARSER_VERSION,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def _parse_payload(content: bytes) -> dict:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # 结构变化防御
        raise ValueError("snapshot content is not valid JSON payload") from exc
    if not isinstance(payload, dict) or not str(payload.get("title", "")).strip():
        raise ValueError("snapshot payload missing required field: title")
    return payload


def _parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _apply_normalization(posting: JobPosting, payload: dict) -> None:
    title = str(payload["title"]).strip()[:255]
    description = str(payload.get("description") or "")

    role_family, role_conf = normalize_role_family(title, description)
    city_code, city_kind = normalize_city(payload.get("city"))
    salary = normalize_salary(payload.get("salary"))
    exp_min, exp_max, exp_type = normalize_experience(payload.get("experience"))
    edu_level, edu_req = normalize_education(payload.get("education"))

    posting.title_raw = title
    posting.title_normalized = normalize_title(title)
    posting.role_family = role_family
    posting.role_family_confidence = role_conf
    posting.description_text = description or None
    posting.city_code = city_code
    posting.city_kind = city_kind
    posting.employment_type = normalize_employment_type(payload.get("employment"))
    posting.salary_min = salary.salary_min
    posting.salary_max = salary.salary_max
    posting.salary_months = salary.months
    posting.salary_unknown = salary.unknown
    posting.salary_raw = salary.raw[:120] if salary.raw else None
    posting.salary_confidence = salary.confidence
    posting.experience_min = exp_min
    posting.experience_max = exp_max
    posting.experience_type = exp_type
    posting.education_level = edu_level
    posting.education_requirement_type = edu_req
    posting.outsourcing_signals = detect_outsourcing_signals(title, description)
    posting.published_at = _parse_published_at(payload.get("published_at"))
    posting.normalizer_version = NORMALIZER_VERSION


def ingest_raw(
    db: Session,
    source: JobSource,
    raw: RawJobSnapshot,
    imported_by_user_id: uuid.UUID | None = None,
) -> IngestResult:
    """单条岗位入库（不 commit，由调用方事务统一提交）。"""
    snapshot = create_snapshot(db, source, raw)
    payload = _parse_payload(raw.content)
    now = _utcnow()

    existing = db.execute(
        select(JobPosting).where(
            JobPosting.job_source_id == source.id,
            JobPosting.source_job_id == raw.ref.source_job_id,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.last_seen_at = now
        prev_snapshot = (
            db.get(JobSnapshot, existing.snapshot_id) if existing.snapshot_id else None
        )
        changed = prev_snapshot is None or prev_snapshot.content_hash != snapshot.content_hash
        if changed:
            # 来源内容实质更新：重跑标准化并指向新快照（历史快照行保留）
            _apply_normalization(existing, payload)
            existing.snapshot_id = snapshot.id
            company = get_or_create_company(db, payload.get("company"))
            existing.company_id = company.id if company else None
        db.flush()
        return IngestResult(
            posting_id=existing.id,
            canonical_job_id=existing.canonical_job_id,
            dedupe_status=existing.dedupe_status,
            created=False,
            changed=changed,
        )

    posting = JobPosting(
        id=uuid.uuid4(),
        job_source_id=source.id,
        source_job_id=raw.ref.source_job_id,
        source_url=raw.ref.url or payload.get("url"),
        snapshot_id=snapshot.id,
        imported_by_user_id=imported_by_user_id,
        first_seen_at=now,
        last_seen_at=now,
        status="active",
        title_raw="",
        title_normalized="",
        normalizer_version=NORMALIZER_VERSION,
    )
    _apply_normalization(posting, payload)
    company = get_or_create_company(db, payload.get("company"))
    posting.company_id = company.id if company else None
    db.add(posting)
    db.flush()

    canonical = None
    if posting.employment_type == "full_time":
        canonical = assign_canonical(db, posting)
    else:
        # 全职过滤：非全职保留证据但不进入 canonical 推荐池
        posting.dedupe_status = "unique"

    logger.info(
        "job_ingested",
        source_key=source.source_key,
        posting_id=str(posting.id),
        dedupe_status=posting.dedupe_status,
        role_family=posting.role_family,
    )
    return IngestResult(
        posting_id=posting.id,
        canonical_job_id=canonical.id if canonical else posting.canonical_job_id,
        dedupe_status=posting.dedupe_status,
        created=True,
        changed=True,
    )


def build_import_text_raw(
    *,
    title: str,
    company: str | None,
    city: str | None,
    salary: str | None,
    experience: str | None,
    education: str | None,
    employment: str | None,
    description: str,
    url: str | None,
) -> RawJobSnapshot:
    """把用户粘贴的岗位正文打包成与连接器一致的原始载荷（同一管道处理）。"""
    from app.jobs.adapters.base import SourceJobRef
    from app.jobs.constants import SOURCE_KEY_USER_IMPORT

    payload = {
        "title": title,
        "company": company,
        "city": city,
        "salary": salary,
        "experience": experience,
        "education": education,
        "employment": employment or "全职",
        "description": description,
        "url": url,
    }
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    ref = SourceJobRef(
        source_key=SOURCE_KEY_USER_IMPORT,
        source_job_id=f"text:{digest}",
        url=url or "",
    )
    return RawJobSnapshot(
        ref=ref,
        content=content,
        media_type="application/json",
        fetched_at=_utcnow(),
        http_meta={"origin": "user_import_text"},
    )


def import_url_reference(
    db: Session,
    source: JobSource,
    url: str,
    imported_by_user_id: uuid.UUID,
    title: str | None = None,
) -> tuple[JobPosting, bool]:
    """URL 导入：只存引用绝不抓取（docs/06 硬边界 + ADR D1）。

    返回 (posting, created)。同一 URL 重复导入幂等复用。
    """
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    source_job_id = f"url:{digest}"
    existing = db.execute(
        select(JobPosting).where(
            JobPosting.job_source_id == source.id,
            JobPosting.source_job_id == source_job_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.last_seen_at = _utcnow()
        return existing, False

    now = _utcnow()
    posting = JobPosting(
        id=uuid.uuid4(),
        job_source_id=source.id,
        source_job_id=source_job_id,
        source_url=url,
        snapshot_id=None,  # 未抓取：没有原始快照
        imported_by_user_id=imported_by_user_id,
        title_raw=(title or "用户导入的岗位链接")[:255],
        title_normalized=normalize_title(title) if title else "",
        role_family="unknown",
        city_kind="unknown",
        employment_type="unknown",
        salary_unknown=True,
        status="unknown",  # 未验证内容有效性
        dedupe_status="unique",
        first_seen_at=now,
        last_seen_at=now,
        normalizer_version=NORMALIZER_VERSION,
    )
    db.add(posting)
    db.flush()
    return posting, True
