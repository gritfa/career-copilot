"""Model Gateway：统一 LLM 调用入口（docs/07 第 7.3 节，ADR-001 裁剪版）。

- ``LLMAdapter`` 协议：真实 DeepSeek 实现与确定性合成实现共用；
  真实 key 一填（DEEPSEEK_API_KEY）即自动切换到 ``DeepSeekAdapter``。
- ``DeepSeekAdapter``：真实 HTTP 实现（OpenAI 兼容 /chat/completions，
  response_format=json_object）。无 key 时 ``configured=False``，
  能力 ``deepseek_generation`` 保持 not_verified，Gateway 绝不调用它。
- ``ModelGateway``：模型 allowlist、供应商授权门控、JSON Schema 二次校验
  （最多一次结构修复）、超时、有限重试、用量记账（usage_ledger 的依据）、
  结构化日志——**绝不写入提示词/简历正文/模型响应正文**。
- 合成实现（app/agents/synthetic.py）确定性：同输入同输出，零网络零费用；
  其产出必须如实标注 not_verified。
"""

import json
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

logger = structlog.get_logger("app.integrations.llm_gateway")

# 需要用户授权（Consent）才能发送数据的真实供应商；合成实现不出本机，不在列
PROVIDERS_REQUIRING_CONSENT = frozenset({"deepseek", "qwen"})

# 每 1M token 估算单价（CNY）：仅用于费用账本估算；合成实现零费用
_PRICE_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    # model_id -> (输入单价, 输出单价)
    "deepseek-chat": (2.0, 8.0),
}


class LLMError(Exception):
    """LLM 调用失败（超时/网络/供应商错误），有限重试后向调用方抛出。

    携带 ``usage``：失败发生前若已有成功的真实调用（如第一次调用成功但
    Schema 错误、修复请求才失败），token 已实际消耗，异常必须带上累计用量，
    调用方照常写 usage_ledger（PR#4 review：失败调用不许丢账）。
    """

    def __init__(self, message: str, usage: "LLMUsage | None" = None) -> None:
        super().__init__(message)
        self.usage = usage


class LLMNotConfiguredError(LLMError):
    """未配置 API key 时禁止调用真实供应商实现（不可重试）。"""


class LLMAuthorizationError(LLMError):
    """真实供应商调用缺少有效用户授权（不可重试，docs/08 第 3 节）。"""


class LLMSchemaError(LLMError):
    """结构化输出经一次修复后仍不符合 Schema（失败不得伪装完成）。

    携带 ``usage``：Schema 失败前的真实调用已实际消耗供应商 token，
    调用方必须照常写 usage_ledger（P0 实跑暴露：失败 run 的费用曾丢账）。
    """


class LLMProviderRejectedError(LLMError):
    """供应商明确拒绝请求（4xx，如无效 key 401 / 无权 403），不可重试。

    P0 失败路径验证要求：无效 key 必须干净地立即失败（不重放浪费额度、
    不静默降级合成、错误信息不含 key/请求体）。429 限流除外（可重试）。
    """


class LLMModelNotAllowedError(LLMError):
    """模型 ID 不在 allowlist（docs/07 第 7.2 节，不可重试）。"""


@dataclass(frozen=True)
class LLMRequest:
    """一次结构化补全请求。``system``/``user`` 含正文，绝不入日志。"""

    system: str
    user: str
    schema_name: str
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class LLMRawResponse:
    """Adapter 原始返回：content 为 JSON 文本；token 计数用于费用账本。"""

    content: str
    tokens_in: int
    tokens_out: int
    provider_request_id: str | None = None


@dataclass(frozen=True)
class LLMUsage:
    """一次 Gateway 调用的用量（写 usage_ledger 的依据；不含任何正文）。"""

    provider: str
    model_id: str
    tokens_in: int
    tokens_out: int
    amount_estimated: float
    attempts: int
    repaired: bool


@runtime_checkable
class LLMAdapter(Protocol):
    """LLM 实现协议。"""

    provider: str
    model_id: str

    @property
    def configured(self) -> bool: ...

    def complete(self, request: LLMRequest) -> LLMRawResponse: ...


@dataclass(frozen=True)
class ConsentDecision:
    """供应商授权决策（由调用方在调用前用 Consent 表判定，Gateway 只做门控）。"""

    allowed: bool
    reason_code: str | None = None


# ---------------- DeepSeek（真实 HTTP 实现） ----------------


class DeepSeekAdapter:
    """DeepSeek OpenAI 兼容 /chat/completions（JSON 输出模式）。

    真实 HTTP 实现；无 key 时 ``configured=False`` 且调用抛
    ``LLMNotConfiguredError``——Gateway 不会选择它。
    该能力在真实 key 验证通过前保持 ``not_verified``。
    """

    provider = "deepseek"

    def __init__(self) -> None:
        settings = get_settings()
        self.model_id = settings.deepseek_model_id
        self._api_key = settings.deepseek_api_key
        self._base_url = settings.deepseek_base_url.rstrip("/")
        self._timeout = settings.llm_timeout_seconds
        self._max_output_tokens = settings.llm_max_output_tokens

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def complete(self, request: LLMRequest) -> LLMRawResponse:
        if not self.configured:
            raise LLMNotConfiguredError("DEEPSEEK_API_KEY 未配置，禁止调用真实供应商")
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model_id,
                    "messages": [
                        {"role": "system", "content": request.system},
                        {"role": "user", "content": request.user},
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": request.max_output_tokens or self._max_output_tokens,
                    "temperature": 0,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # 4xx（429 除外）= 供应商明确拒绝（无效 key/无权/参数错），重试无意义；
            # 错误信息只含状态码，不含请求体/响应体/key
            if 400 <= status < 500 and status != 429:
                raise LLMProviderRejectedError(
                    f"deepseek rejected request: HTTP {status}"
                ) from exc
            raise LLMError(f"deepseek call failed: HTTP {status}") from exc
        except httpx.HTTPError as exc:
            # 只记异常类型，不把请求体/响应体写日志（可能含正文）
            raise LLMError(f"deepseek call failed: {type(exc).__name__}") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("deepseek returned unexpected payload shape") from exc
        return LLMRawResponse(
            content=content,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            provider_request_id=payload.get("id"),
        )

    async def probe_runtime(self) -> bool:
        """运行可用性轻量探测（capabilities 三态之「运行可用」）。

        GET /models：零 token 费用，验证 key 当下有效且服务可达。
        任何失败（网络/4xx/5xx）→ False，绝不抛异常、绝不记录响应正文。
        异步实现（httpx.AsyncClient）：/health/capabilities 在事件循环内调用，
        同步阻塞版最坏会卡住整个事件循环 10s（PR#4 review 第 3 条）。
        """
        if not self.configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=min(self._timeout, 10.0)) as client:
                response = await client.get(
                    f"{self._base_url}/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError:
            return False
        return response.status_code == 200


# ---------------- Gateway ----------------


def _estimate_amount(provider: str, model_id: str, tokens_in: int, tokens_out: int) -> float:
    if provider == "synthetic":
        return 0.0
    price_in, price_out = _PRICE_PER_1M_TOKENS.get(model_id, (0.0, 0.0))
    return round(tokens_in / 1_000_000 * price_in + tokens_out / 1_000_000 * price_out, 6)


class ModelGateway:
    """统一入口：allowlist、授权门控、有限重试、Schema 校验 + 一次修复、记账。

    调用方拿到 ``LLMUsage`` 后写 usage_ledger（Gateway 不直接依赖 DB，
    便于任务层/评测复用，与 EmbeddingGateway 同构）。
    """

    def __init__(self, adapter: LLMAdapter | None = None) -> None:
        self.adapter = adapter or get_llm_adapter()
        settings = get_settings()
        self._max_retries = settings.llm_max_retries
        self._allowlist = list(settings.llm_model_allowlist)

    def complete_json[TModel: BaseModel](
        self,
        request: LLMRequest,
        output_model: type[TModel],
        *,
        consent: ConsentDecision | None = None,
    ) -> tuple[TModel, LLMUsage]:
        """结构化补全：返回校验过的模型对象与用量。

        - 模型不在 allowlist / 未配置 / 未授权 → 立即抛错，不重试；
        - 供应商错误 → 有限重试；
        - JSON/Schema 失败 → 最多一次结构修复，仍失败抛 ``LLMSchemaError``。
        """
        if self.adapter.model_id not in self._allowlist:
            raise LLMModelNotAllowedError(
                f"model {self.adapter.model_id} not in allowlist"
            )
        if not self.adapter.configured:
            raise LLMNotConfiguredError("llm adapter not configured")
        if self.adapter.provider in PROVIDERS_REQUIRING_CONSENT and (
            consent is None or not consent.allowed
        ):
            # 备用切换也不是例外：每个真实供应商调用前都必须有独立授权
            raise LLMAuthorizationError(
                f"consent required for provider {self.adapter.provider}"
            )

        attempts = 0
        tokens_in = 0
        tokens_out = 0

        def _call(req: LLMRequest) -> LLMRawResponse:
            nonlocal attempts, tokens_in, tokens_out
            raw = self._complete_with_retry(req)
            attempts += 1
            tokens_in += raw.tokens_in
            tokens_out += raw.tokens_out
            return raw

        def _accumulated_usage(*, repaired: bool) -> LLMUsage:
            return LLMUsage(
                provider=self.adapter.provider,
                model_id=self.adapter.model_id,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                amount_estimated=_estimate_amount(
                    self.adapter.provider, self.adapter.model_id, tokens_in, tokens_out
                ),
                attempts=attempts,
                repaired=repaired,
            )

        raw = _call(request)
        parsed, error_note = self._try_validate(raw.content, output_model)
        repaired = False
        if parsed is None:
            # 最多一次结构修复：把校验错误类别（不含正文）回给模型重试
            repaired = True
            repair_request = LLMRequest(
                system=request.system,
                user=(
                    f"{request.user}\n\n"
                    "上一次输出不是合法的目标 JSON 结构"
                    f"（错误类别：{error_note}）。"
                    "请只输出符合要求的 JSON，不要输出任何其他内容。"
                ),
                schema_name=request.schema_name,
                max_output_tokens=request.max_output_tokens,
            )
            try:
                raw = _call(repair_request)
            except LLMError as exc:
                # PR#4 review 第 2 条：第一次调用成功（token 已实际消耗）但 Schema
                # 错误，修复请求本身失败（网络/401/5xx）时，第一次的用量曾经丢账。
                # 该分支同样携带累计用量，调用方照常写 usage_ledger。
                if exc.usage is None:
                    exc.usage = _accumulated_usage(repaired=True)
                raise
            parsed, error_note = self._try_validate(raw.content, output_model)
            if parsed is None:
                logger.warning(
                    "llm_schema_failed",
                    provider=self.adapter.provider,
                    model_id=self.adapter.model_id,
                    schema_name=request.schema_name,
                    error_note=error_note,
                )
                raise LLMSchemaError(
                    f"output failed schema {request.schema_name} after one repair",
                    usage=_accumulated_usage(repaired=True),
                )

        usage = _accumulated_usage(repaired=repaired)
        # 结构化日志：只含计数/模型/Schema 名，绝不含提示词或响应正文
        logger.info(
            "llm_completed",
            provider=usage.provider,
            model_id=usage.model_id,
            schema_name=request.schema_name,
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            attempts=usage.attempts,
            repaired=usage.repaired,
        )
        return parsed, usage

    @staticmethod
    def _try_validate[TModel: BaseModel](
        content: str, output_model: type[TModel]
    ) -> tuple[TModel | None, str | None]:
        """解析 + Schema 校验；失败返回 (None, 错误类别)，错误信息不含正文。"""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None, "invalid_json"
        try:
            return output_model.model_validate(data), None
        except ValidationError as exc:
            # 只取字段路径与错误类型，不取输入值
            kinds = sorted(
                {
                    f"{'.'.join(str(p) for p in err['loc'])}:{err['type']}"
                    for err in exc.errors()
                }
            )
            return None, ";".join(kinds[:5]) or "schema_mismatch"

    def _complete_with_retry(self, request: LLMRequest) -> LLMRawResponse:
        last_error: LLMError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self.adapter.complete(request)
            except (
                LLMNotConfiguredError,
                LLMAuthorizationError,
                LLMProviderRejectedError,
            ):
                raise  # 不可重试（未配置/未授权/供应商明确拒绝）
            except LLMError as exc:
                last_error = exc
                logger.warning(
                    "llm_call_retry",
                    provider=self.adapter.provider,
                    model_id=self.adapter.model_id,
                    attempt=attempt,
                    error_type=type(exc).__name__,
                )
                if attempt < self._max_retries:
                    time.sleep(min(2.0, 0.2 * (2**attempt)))
        assert last_error is not None
        raise last_error


def get_llm_adapter() -> LLMAdapter:
    """选择实现：有 DEEPSEEK_API_KEY 用真实 DeepSeek；否则确定性合成实现。"""
    deepseek = DeepSeekAdapter()
    if deepseek.configured:
        return deepseek
    # 函数级导入避免 integrations -> agents 的循环依赖
    from app.agents.synthetic import SyntheticAnalysisAdapter

    return SyntheticAnalysisAdapter()
