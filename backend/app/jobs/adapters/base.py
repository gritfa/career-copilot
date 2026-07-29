"""JobSourceAdapter 端口（docs/06 第 4 节）。

Adapter 只返回原始快照与来源元数据；标准化、去重、匹配属于独立服务，
连接器不得直接写 Recommendation，也不得绕过登录/验证码/频率限制。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

ValidityStatus = Literal["active", "inactive", "unknown"]


@dataclass(frozen=True)
class SourceJobRef:
    """来源内稳定岗位引用。"""

    source_key: str
    source_job_id: str
    url: str


@dataclass(frozen=True)
class DiscoveryPage:
    """一页发现结果 + 下一页游标（None 表示结束）。"""

    refs: list[SourceJobRef]
    next_cursor: str | None


@dataclass(frozen=True)
class RawJobSnapshot:
    """原始详情载荷：只承载原始内容与 HTTP 元数据，不做任何解释。"""

    ref: SourceJobRef
    content: bytes
    media_type: str
    fetched_at: datetime
    http_meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidityResult:
    """有效性三态：登录墙/临时错误 → unknown，不得直接判下架。"""

    status: ValidityStatus
    checked_at: datetime
    reason: str | None = None


@dataclass(frozen=True)
class SourcePolicy:
    """来源政策快照：能力状态必须如实（未调查一律 not_verified）。"""

    source_key: str
    source_type: str  # company_site / platform / user_import
    allowed_modes: tuple[str, ...]  # fixture / api / public_html / link_out / import_only
    policy_status: str  # not_verified / verified
    access_policy_url: str | None = None
    rate_limit_per_minute: int = 6
    notes: str | None = None


class SourceAccessError(Exception):
    """来源访问错误基类：携带稳定错误码，供 run 统计与熔断分类。"""

    error_code = "SOURCE_ERROR"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.error_code)


class TemporarySourceError(SourceAccessError):
    """临时错误（网络/5xx）：有限退避重试，不立即熔断。"""

    error_code = "SOURCE_TEMPORARY_ERROR"


class CaptchaRequiredError(SourceAccessError):
    """出现验证码：自动化路径立即停止并熔断（规格硬边界）。"""

    error_code = "CAPTCHA_REQUIRED"


class AccessForbiddenError(SourceAccessError):
    """403/明确禁止访问：立即熔断。"""

    error_code = "ACCESS_FORBIDDEN"


@runtime_checkable
class JobSourceAdapter(Protocol):
    """连接器端口（docs/06 第 4 节建议签名）。"""

    source_key: str

    async def discover(self, cursor: str | None) -> DiscoveryPage: ...

    async def fetch_detail(self, ref: SourceJobRef) -> RawJobSnapshot: ...

    async def check_validity(self, ref: SourceJobRef) -> ValidityResult: ...

    def policy(self) -> SourcePolicy: ...
