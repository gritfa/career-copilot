"""pgvector 向量集成测试（真实 PG + pgvector 0.8.5）。

覆盖：向量入库（模型/维度/预处理版本）、版本隔离（禁止新旧混用）、
余弦召回正确性（与 Python 端点积逐一对照）、文本未变不重算。
"""

import math
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tests.integration.conftest import create_user
from tests.integration.test_matching_pipeline import create_job, create_plan


def sync_session():
    from app.core.config import get_settings

    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    return Session(engine), engine


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb)


async def test_vector_upsert_versioning_and_cosine_recall(db_factory, real_env):
    from app.db.models import EMBEDDING_DIM, JobVector
    from app.integrations.embedding import DeterministicHashEmbeddingAdapter, EmbeddingGateway
    from app.matching.recall import recall_top_k, upsert_job_vector, upsert_profile_vector
    from app.matching.vector_text import PREPROCESS_VERSION

    user = await create_user(db_factory, "vec@cc-integration.dev")
    plan = await create_plan(db_factory, user)
    job_similar = await create_job(
        db_factory,
        title="Python 后端开发工程师",
        description="FastAPI PostgreSQL Redis 异步服务开发",
    )
    job_different = await create_job(
        db_factory,
        title="前端开发工程师",
        description="React TypeScript 组件库 Webpack 前端工程化",
        role_family="frontend",
    )

    adapter = DeterministicHashEmbeddingAdapter()
    gateway = EmbeddingGateway(adapter)
    profile_text = "求职方向 Python 后端开发\n技能 Python FastAPI PostgreSQL Redis 异步"
    text_similar = "Python 后端开发工程师\nFastAPI PostgreSQL Redis 异步服务开发"
    text_different = "前端开发工程师\nReact TypeScript 组件库 Webpack 前端工程化"

    db, engine = sync_session()
    try:
        pvec, usage = upsert_profile_vector(db, gateway, plan.id, profile_text)
        assert usage is not None and usage.provider == "deterministic"
        jv1, _ = upsert_job_vector(db, gateway, job_similar, text_similar)
        jv2, _ = upsert_job_vector(db, gateway, job_different, text_different)
        db.commit()

        # 入库元数据：模型 ID / 维度 / 预处理版本
        assert jv1.model_id == "det-hash-768@1"
        assert jv1.dim == EMBEDDING_DIM
        assert jv1.preprocess_version == PREPROCESS_VERSION

        # 幂等：文本未变不重算（usage 为 None）
        jv1_again, usage_again = upsert_job_vector(db, gateway, job_similar, text_similar)
        assert jv1_again.id == jv1.id and usage_again is None

        # 版本隔离：同岗位手工插入"旧模型"向量（唯一键允许共存）
        old_vec = [0.0] * EMBEDDING_DIM
        old_vec[0] = 1.0
        db.add(
            JobVector(
                id=uuid.uuid4(),
                canonical_job_id=job_similar,
                embedding=old_vec,
                model_id="old-model@0",
                dim=EMBEDDING_DIM,
                preprocess_version="p0",
                source_text_hash="0" * 64,
            )
        )
        db.commit()

        # 召回：只取当前 model_id + preprocess_version 的向量
        query_vec = adapter.embed_batch([profile_text])[0]
        hits = recall_top_k(
            db,
            query_vec,
            model_id=adapter.model_id,
            preprocess_version=PREPROCESS_VERSION,
            candidate_ids=[job_similar, job_different],
            k=50,
        )
        assert len(hits) == 2  # 旧模型向量绝不混入（否则 job_similar 会出现两次）
        by_job = {h.canonical_job_id: h.similarity for h in hits}

        # 余弦正确性：与 Python 端逐一对照（同一确定性向量）
        expected_similar = cosine(query_vec, adapter.embed_batch([text_similar])[0])
        expected_different = cosine(query_vec, adapter.embed_batch([text_different])[0])
        assert abs(by_job[job_similar] - expected_similar) < 1e-6
        assert abs(by_job[job_different] - expected_different) < 1e-6
        # 已知相似度排序：词面重叠多的岗位相似度更高，且排在第一
        assert by_job[job_similar] > by_job[job_different]
        assert hits[0].canonical_job_id == job_similar

        # 老版本向量单独查询仍然存在（不删除、不混用）
        old_rows = db.execute(
            select(JobVector).where(JobVector.model_id == "old-model@0")
        ).scalars().all()
        assert len(old_rows) == 1
    finally:
        db.close()
        engine.dispose()


async def test_profile_vector_updates_when_text_changes(db_factory, real_env):
    from app.integrations.embedding import DeterministicHashEmbeddingAdapter, EmbeddingGateway
    from app.matching.recall import upsert_profile_vector

    user = await create_user(db_factory, "vec-update@cc-integration.dev")
    plan = await create_plan(db_factory, user)
    gateway = EmbeddingGateway(DeterministicHashEmbeddingAdapter())

    db, engine = sync_session()
    try:
        v1, usage1 = upsert_profile_vector(db, gateway, plan.id, "技能 Python")
        db.commit()
        hash1 = v1.source_text_hash
        v2, usage2 = upsert_profile_vector(db, gateway, plan.id, "技能 Python Kubernetes")
        db.commit()
        assert usage1 is not None and usage2 is not None  # 文本变化 → 重算
        assert v1.id == v2.id  # 同一（方案, 模型, 版本）行原地更新，不产生第二行
        assert v2.source_text_hash != hash1  # hash 已更新
    finally:
        db.close()
        engine.dispose()
