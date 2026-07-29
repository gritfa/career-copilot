"""Embedding Adapter/Gateway（docs/07 第 3 节、第 7.3 节 Gateway 要求）。

- ``EmbeddingAdapter`` 协议：真实阿里云百炼实现与确定性合成实现共用。
- ``AliyunBailianAdapter``：真实 HTTP 实现（DashScope 兼容模式 /embeddings）。
  无 API key 时 ``configured=False``，能力 ``aliyun_embedding`` 保持 not_verified，
  Gateway 绝不调用它（00-master：不虚标未验证能力）。
- ``DeterministicHashEmbeddingAdapter``：分词 + 特征哈希到固定 768 维单位向量，
  离线可复现（同文本同向量），用于无 key 环境打通召回管道。
- ``EmbeddingGateway``：批量、超时、有限重试、费用记账（usage_ledger）、
  日志只含计数/模型 ID/哈希——**绝不写入向量文本正文**。
"""

import hashlib
import math
import re
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx
import structlog

from app.core.config import get_settings
from app.db.models import EMBEDDING_DIM

logger = structlog.get_logger("app.integrations.embedding")

# 每 1K token 估算单价（CNY）；确定性合成实现零费用
_ALIYUN_PRICE_PER_1K_TOKENS = 0.0005


class EmbeddingError(Exception):
    """Embedding 调用失败（超时/网络/供应商错误），调用方可降级。"""


class EmbeddingNotConfiguredError(EmbeddingError):
    """未配置 API key 时禁止调用真实供应商实现。"""


@runtime_checkable
class EmbeddingAdapter(Protocol):
    """Embedding 实现协议。"""

    provider: str
    model_id: str
    dim: int

    @property
    def configured(self) -> bool: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


# ---------------- 阿里云百炼（真实 HTTP 实现） ----------------


class AliyunBailianAdapter:
    """阿里云百炼 text-embedding-v3（OpenAI 兼容模式，dimensions=768）。

    真实 HTTP 实现；无 key 时 ``configured=False`` 且调用抛
    ``EmbeddingNotConfiguredError``——Gateway 不会选择它。
    该能力在真实 key 验证通过前保持 ``not_verified``。
    """

    provider = "aliyun_bailian"

    def __init__(self) -> None:
        settings = get_settings()
        self.model_id = settings.embedding_model_id
        self.dim = EMBEDDING_DIM
        self._api_key = settings.dashscope_api_key
        self._base_url = settings.dashscope_base_url.rstrip("/")
        self._timeout = settings.embedding_timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not self.configured:
            raise EmbeddingNotConfiguredError("DASHSCOPE_API_KEY 未配置，禁止调用真实供应商")
        try:
            response = httpx.post(
                f"{self._base_url}/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model_id,
                    "input": texts,
                    "dimensions": self.dim,
                    "encoding_format": "float",
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            # 不把请求体/响应体写日志（可能含正文）
            raise EmbeddingError(f"aliyun embedding failed: {type(exc).__name__}") from exc
        data = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
        vectors = [item["embedding"] for item in data]
        if len(vectors) != len(texts):
            raise EmbeddingError("aliyun embedding returned unexpected item count")
        return vectors


# ---------------- 确定性合成实现（离线可复现） ----------------

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9+#.]{2,}")
_CJK_RE = re.compile(r"[一-鿿]")


def _tokenize(text: str) -> list[str]:
    """分词：ASCII 词（≥2 字符）+ 中文单字与二元组，确定性且无外部依赖。"""
    lowered = text.lower()
    tokens = _ASCII_TOKEN_RE.findall(lowered)
    cjk_chars = _CJK_RE.findall(lowered)
    tokens.extend(cjk_chars)
    tokens.extend(a + b for a, b in zip(cjk_chars, cjk_chars[1:], strict=False))
    return tokens


class DeterministicHashEmbeddingAdapter:
    """特征哈希 Embedding：sha256(token) → 桶下标与符号，累加后 L2 归一。

    性质（有单元测试保障）：
    - 确定性：同文本任何时刻产出完全相同的 768 维单位向量；
    - 词面重叠越多余弦相似度越高，可支撑召回排序测试；
    - 零网络、零费用。
    """

    provider = "deterministic"
    model_id = "det-hash-768@1"
    dim = EMBEDDING_DIM

    @property
    def configured(self) -> bool:
        return True

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            # 空文本：固定单位向量（首维为 1），仍可复现
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


# ---------------- Gateway ----------------


@dataclass(frozen=True)
class EmbeddingUsage:
    """一次 embed 调用的用量（写入 usage_ledger 的依据）。"""

    provider: str
    model_id: str
    texts_count: int
    tokens_estimated: int
    amount_estimated: float


class EmbeddingGateway:
    """统一入口：批量、有限重试、费用记账、无正文日志。

    ``record_usage`` 回调由调用方提供（同步 Session 写 usage_ledger），
    Gateway 不直接依赖 DB，便于离线评测复用。
    """

    def __init__(self, adapter: EmbeddingAdapter | None = None) -> None:
        self.adapter = adapter or get_embedding_adapter()
        settings = get_settings()
        self._batch_size = settings.embedding_batch_size
        self._max_retries = settings.embedding_max_retries

    def embed(self, texts: list[str]) -> tuple[list[list[float]], EmbeddingUsage]:
        """批量 embed；返回向量与用量。失败在有限重试后抛 EmbeddingError。"""
        if not self.adapter.configured:
            raise EmbeddingNotConfiguredError("embedding adapter not configured")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(self._embed_batch_with_retry(batch))
        # 中文文本 token 估算：约 1 token/字符（保守），仅用于费用账本
        tokens = sum(len(t) for t in texts)
        amount = (
            0.0
            if self.adapter.provider == "deterministic"
            else round(tokens / 1000 * _ALIYUN_PRICE_PER_1K_TOKENS, 6)
        )
        usage = EmbeddingUsage(
            provider=self.adapter.provider,
            model_id=self.adapter.model_id,
            texts_count=len(texts),
            tokens_estimated=tokens,
            amount_estimated=amount,
        )
        logger.info(
            "embedding_completed",
            provider=usage.provider,
            model_id=usage.model_id,
            texts_count=usage.texts_count,
            tokens_estimated=usage.tokens_estimated,
        )
        return vectors, usage

    def _embed_batch_with_retry(self, batch: list[str]) -> list[list[float]]:
        last_error: EmbeddingError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self.adapter.embed_batch(batch)
            except EmbeddingNotConfiguredError:
                raise  # 不可重试
            except EmbeddingError as exc:
                last_error = exc
                logger.warning(
                    "embedding_batch_retry",
                    provider=self.adapter.provider,
                    model_id=self.adapter.model_id,
                    attempt=attempt,
                    error_type=type(exc).__name__,
                )
                if attempt < self._max_retries:
                    time.sleep(min(2.0, 0.2 * (2**attempt)))
        assert last_error is not None
        raise last_error


def get_embedding_adapter() -> EmbeddingAdapter:
    """选择实现：有 DashScope key 用真实阿里云；否则确定性合成实现。"""
    aliyun = AliyunBailianAdapter()
    if aliyun.configured:
        return aliyun
    return DeterministicHashEmbeddingAdapter()
