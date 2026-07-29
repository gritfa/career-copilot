"""pgvector 召回：向量入库（版本化）与余弦相似 Top-K（docs/07 第 3 节）。

- 向量与 model_id / dim / preprocess_version 一起保存；唯一键含版本，
  召回查询强制按版本过滤——**禁止新旧模型向量混用**。
- 全部使用同步 Session（Celery 任务内），与 app/jobs/tasks.py 同模式。
"""

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobVector, ProfileVector
from app.integrations.embedding import EmbeddingGateway, EmbeddingUsage
from app.matching.vector_text import PREPROCESS_VERSION, text_hash

logger = structlog.get_logger("app.matching.recall")


@dataclass(frozen=True)
class RecallHit:
    canonical_job_id: uuid.UUID
    similarity: float  # 余弦相似度 [-1, 1]


def upsert_job_vector(
    db: Session, gateway: EmbeddingGateway, canonical_job_id: uuid.UUID, text: str
) -> tuple[JobVector, EmbeddingUsage | None]:
    """按（岗位, 模型, 预处理版本）幂等写入；文本未变不重算。"""
    adapter = gateway.adapter
    digest = text_hash(text)
    existing = db.execute(
        select(JobVector).where(
            JobVector.canonical_job_id == canonical_job_id,
            JobVector.model_id == adapter.model_id,
            JobVector.preprocess_version == PREPROCESS_VERSION,
        )
    ).scalar_one_or_none()
    if existing is not None and existing.source_text_hash == digest:
        return existing, None
    vectors, usage = gateway.embed([text])
    if existing is None:
        existing = JobVector(
            id=uuid.uuid4(),
            canonical_job_id=canonical_job_id,
            embedding=vectors[0],
            model_id=adapter.model_id,
            dim=adapter.dim,
            preprocess_version=PREPROCESS_VERSION,
            source_text_hash=digest,
        )
        db.add(existing)
    else:
        existing.embedding = vectors[0]
        existing.source_text_hash = digest
        existing.dim = adapter.dim
    db.flush()
    return existing, usage


def upsert_profile_vector(
    db: Session, gateway: EmbeddingGateway, search_plan_id: uuid.UUID, text: str
) -> tuple[ProfileVector, EmbeddingUsage | None]:
    """简历侧（方案级）向量幂等写入；文本未变不重算。"""
    adapter = gateway.adapter
    digest = text_hash(text)
    existing = db.execute(
        select(ProfileVector).where(
            ProfileVector.search_plan_id == search_plan_id,
            ProfileVector.model_id == adapter.model_id,
            ProfileVector.preprocess_version == PREPROCESS_VERSION,
        )
    ).scalar_one_or_none()
    if existing is not None and existing.source_text_hash == digest:
        return existing, None
    vectors, usage = gateway.embed([text])
    if existing is None:
        existing = ProfileVector(
            id=uuid.uuid4(),
            search_plan_id=search_plan_id,
            embedding=vectors[0],
            model_id=adapter.model_id,
            dim=adapter.dim,
            preprocess_version=PREPROCESS_VERSION,
            source_text_hash=digest,
        )
        db.add(existing)
    else:
        existing.embedding = vectors[0]
        existing.source_text_hash = digest
        existing.dim = adapter.dim
    db.flush()
    return existing, usage


def recall_top_k(
    db: Session,
    query_embedding,
    *,
    model_id: str,
    preprocess_version: str = PREPROCESS_VERSION,
    candidate_ids: list[uuid.UUID] | None = None,
    k: int = 50,
) -> list[RecallHit]:
    """余弦相似 Top-K；强制按 model_id + preprocess_version 过滤（版本隔离）。"""
    distance = JobVector.embedding.cosine_distance(query_embedding)
    stmt = (
        select(JobVector.canonical_job_id, distance.label("distance"))
        .where(
            JobVector.model_id == model_id,
            JobVector.preprocess_version == preprocess_version,
        )
        .order_by(distance)
        .limit(k)
    )
    if candidate_ids is not None:
        if not candidate_ids:
            return []
        stmt = stmt.where(JobVector.canonical_job_id.in_(candidate_ids))
    rows = db.execute(stmt).all()
    hits = [
        RecallHit(canonical_job_id=row.canonical_job_id, similarity=1.0 - float(row.distance))
        for row in rows
    ]
    logger.info(
        "vector_recall",
        model_id=model_id,
        preprocess_version=preprocess_version,
        candidates=len(candidate_ids) if candidate_ids is not None else None,
        hits=len(hits),
    )
    return hits
