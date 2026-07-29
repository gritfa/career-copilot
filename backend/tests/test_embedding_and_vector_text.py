"""Embedding Adapter / 向量文本单元测试（离线、确定性）。

关键约束：
- 受保护属性与联系方式绝不进入向量文本（确定性断言）。
- 确定性合成 Adapter：同文本同向量、单位范数、可复现。
- 无 key 时不选择/不调用真实阿里云实现。
"""

import math
from types import SimpleNamespace

import pytest

from app.integrations.embedding import (
    AliyunBailianAdapter,
    DeterministicHashEmbeddingAdapter,
    EmbeddingGateway,
    EmbeddingNotConfiguredError,
    get_embedding_adapter,
)
from app.matching.vector_text import (
    build_job_vector_text,
    build_profile_vector_text,
    scrub_contact,
)


def fact(fact_type, value):
    return SimpleNamespace(fact_type=fact_type, value_json=value, id="f-1")


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


# ---------------- 向量文本：受保护属性/联系方式硬排除 ----------------


def test_profile_vector_text_excludes_contact_and_protected():
    facts = [
        fact("skill", {"name": "Python", "detail": "FastAPI 异步开发"}),
        fact("contact_email", {"email": "zhangsan@example.com"}),
        fact("contact_phone", {"phone": "13812345678"}),
        # 防御性：即使上游漏过受保护类型，也不得进入向量文本
        fact("gender", {"value": "男"}),
        fact("age", {"value": "27"}),
        fact("marital_status", {"value": "已婚"}),
        fact("native_place", {"value": "河南"}),
        fact("education", {"school": "北京邮电大学", "degree": "硕士"}),
        fact("work_experience", {"company": "智澜科技有限公司"}),
    ]
    text = build_profile_vector_text(
        role_family="ai_application",
        city_codes=["110100"],
        work_modes=["onsite"],
        facts=facts,
    )
    assert "zhangsan@example.com" not in text
    assert "13812345678" not in text
    assert "男" not in text
    assert "27" not in text
    assert "已婚" not in text
    assert "河南" not in text
    # 应保留的内容
    assert "Python" in text and "FastAPI" in text
    assert "北京邮电大学" in text and "硕士" in text
    assert "智澜科技有限公司" in text


def test_scrub_contact_removes_slipped_email_and_phone():
    dirty = "技能 Python 联系我 foo.bar@qq.com 或 139-1234-5678 或 139****5310"
    cleaned = scrub_contact(dirty)
    assert "foo.bar@qq.com" not in cleaned
    assert "139" not in cleaned.replace(" ", "") or "1234" not in cleaned
    assert "Python" in cleaned


def test_job_vector_text_is_title_plus_description():
    posting = SimpleNamespace(
        title_raw="Python 后端开发工程师",
        title_normalized="python后端开发工程师",
        description_text="负责 FastAPI 服务开发",
    )
    text = build_job_vector_text(posting)
    assert "Python 后端开发工程师" in text
    assert "FastAPI" in text


# ---------------- 确定性合成 Adapter ----------------


def test_deterministic_adapter_reproducible_unit_vectors():
    adapter = DeterministicHashEmbeddingAdapter()
    v1 = adapter.embed_batch(["Python FastAPI 后端开发"])[0]
    v2 = adapter.embed_batch(["Python FastAPI 后端开发"])[0]
    assert v1 == v2  # 完全可复现
    assert len(v1) == 768
    assert math.isclose(math.sqrt(sum(x * x for x in v1)), 1.0, rel_tol=1e-9)


def test_deterministic_adapter_similarity_orders_by_overlap():
    adapter = DeterministicHashEmbeddingAdapter()
    profile, similar, different = adapter.embed_batch(
        [
            "python fastapi redis postgresql 后端开发",
            "python fastapi postgresql 服务开发",
            "java spring kafka 前端 react",
        ]
    )
    assert cosine(profile, similar) > cosine(profile, different)


# ---------------- 无 key：真实实现不被选择、不可调用 ----------------


def test_aliyun_adapter_not_configured_without_key():
    adapter = AliyunBailianAdapter()
    assert adapter.configured is False  # 测试环境无 DASHSCOPE_API_KEY
    with pytest.raises(EmbeddingNotConfiguredError):
        adapter.embed_batch(["任何文本"])


def test_gateway_selects_deterministic_adapter_without_key():
    adapter = get_embedding_adapter()
    assert isinstance(adapter, DeterministicHashEmbeddingAdapter)
    gateway = EmbeddingGateway()
    vectors, usage = gateway.embed(["文本一", "文本二"])
    assert len(vectors) == 2
    assert usage.provider == "deterministic"
    assert usage.amount_estimated == 0.0
    assert usage.tokens_estimated > 0
