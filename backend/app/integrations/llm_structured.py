"""LLM 结构化抽取接口 + 合成实现（无真实模型 key，能力保持 not_verified）。

真实 DeepSeek/Qwen 结构化抽取接入时实现同一协议，并且必须：
- 每次调用前经过 ModelAuthorizationGuard（app/consents/guard.py）；
- 不把简历正文写入日志/审计。
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CandidateDraft:
    """抽取出的候选事实草稿（尚未入库）。"""

    fact_type: str
    value_json: dict[str, Any]
    span_start: int
    span_end: int
    quote: str
    confidence: float


@dataclass
class ExtractionOutput:
    """一次结构化抽取的结果。"""

    candidates: list[CandidateDraft] = field(default_factory=list)
    protected_discarded_count: int = 0


@runtime_checkable
class StructuredExtractionAdapter(Protocol):
    """结构化抽取协议：规则解析器与（未来的）LLM 实现共用。"""

    parser_name: str
    parser_version: str
    model_provider: str | None
    model_version: str | None

    def extract(self, text: str) -> ExtractionOutput: ...


class SyntheticStructuredAdapter:
    """合成 LLM 结构化实现：确定性、离线、无网络调用。

    用途：在没有真实模型 key 时打通「LLM 结构化抽取」这条管道的接口层。
    内部委托确定性规则解析器，输出与真实实现同构；
    对应能力（deepseek_generation / qwen_fallback）保持 not_verified。
    """

    parser_name = "synthetic-structured"
    parser_version = "1.0.0"
    model_provider: str | None = "synthetic"
    model_version: str | None = "synthetic-0"

    def extract(self, text: str) -> ExtractionOutput:
        # 函数级导入避免 integrations -> resumes 的循环依赖
        from app.resumes.parser import RuleBasedExtractor

        return RuleBasedExtractor().extract(text)
