"""离线单元测试：Storage Adapter 协议、key 安全、合成结构化 Adapter、受保护属性过滤。"""

import pytest

from app.integrations.llm_structured import (
    ExtractionOutput,
    StructuredExtractionAdapter,
    SyntheticStructuredAdapter,
)
from app.integrations.malware import NullMalwareScanner
from app.integrations.storage import (
    LocalStorageAdapter,
    OSSStorageAdapter,
    StorageAdapter,
    StorageKeyError,
    validate_key,
)
from app.resumes.parser import (
    PROTECTED_FACT_TYPES,
    RuleBasedExtractor,
    filter_protected,
)

SAMPLE = (
    "李示例 — 测试工程师\n"
    "性别：女 | 年龄：26 | 籍贯：广东\n"
    "邮箱：test.sample@example.com | 手机：13800001111\n"
    "2016.09 – 2020.06 华南示例大学 软件工程 本科\n"
    "## 专业技能\n"
    "- 测试：pytest、接口自动化\n"
    "2020.07 – 至今 广州示例科技有限公司 测试开发工程师\n"
)


def test_storage_key_validation_blocks_unsafe_keys():
    validate_key("resumes/2b1c/abc123.pdf")
    for bad in ("../etc/passwd", "resumes/../../x", "a@b.com/file.pdf", "Resumes/UPPER"):
        with pytest.raises(StorageKeyError):
            validate_key(bad)


def test_local_storage_roundtrip(tmp_path):
    storage = LocalStorageAdapter(root=tmp_path)
    assert isinstance(storage, StorageAdapter)
    key = "resumes/u1/f1.pdf"
    assert not storage.exists(key)
    storage.save(key, b"data")
    assert storage.exists(key)
    assert storage.open(key) == b"data"
    storage.delete(key)
    assert not storage.exists(key)
    storage.delete(key)  # 幂等

    with pytest.raises(StorageKeyError):
        storage.save("../escape.bin", b"x")


def test_oss_adapter_is_honest_stub():
    oss = OSSStorageAdapter()
    assert isinstance(oss, StorageAdapter)
    with pytest.raises(NotImplementedError):
        oss.save("resumes/x/y.pdf", b"")
    with pytest.raises(NotImplementedError):
        oss.exists("resumes/x/y.pdf")


def test_null_malware_scanner_reports_skipped():
    assert NullMalwareScanner().scan(b"anything") == "skipped_not_configured"


def test_synthetic_adapter_implements_llm_interface_deterministically():
    adapter = SyntheticStructuredAdapter()
    assert isinstance(adapter, StructuredExtractionAdapter)
    out1 = adapter.extract(SAMPLE)
    out2 = adapter.extract(SAMPLE)
    assert isinstance(out1, ExtractionOutput)
    # 确定性：两次抽取结果一致
    assert [(c.fact_type, c.value_json) for c in out1.candidates] == [
        (c.fact_type, c.value_json) for c in out2.candidates
    ]
    types = {c.fact_type for c in out1.candidates}
    assert {"contact_email", "contact_phone", "education", "work_experience"} <= types
    # 每个候选带 span 与 confidence，引文与 span 一致
    for c in out1.candidates:
        assert SAMPLE[c.span_start : c.span_end].strip() == c.quote
        assert 0 < c.confidence <= 1


def test_protected_attributes_discarded_and_counted():
    out = filter_protected(RuleBasedExtractor().extract(SAMPLE))
    assert out.protected_discarded_count >= 3  # 性别/年龄/籍贯
    assert not ({c.fact_type for c in out.candidates} & PROTECTED_FACT_TYPES)
    # 值里不携带受保护内容
    for c in out.candidates:
        for token in ("性别", "年龄", "籍贯"):
            assert token not in repr(c.value_json)
