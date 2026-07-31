"""Spike 配置：sources.yaml / thresholds.yaml 的加载、校验与写回。"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 仓库根 = backend/app/supply_spike/config.py 往上三级
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SPIKE_DIR = REPO_ROOT / "supply-spike"

VALID_STATUSES = ("verified", "not_verified", "blocked")

_SOURCES_HEADER = """\
# 供给 Spike 来源清单（probe 工具自动写回 probe_result / status / status_reason，
# 其余字段人工维护；合规硬边界见 supply-spike/README.md）。
# status 只可人工收紧（verified→not_verified/blocked），不可人工放宽。
"""


@dataclass
class RateLimitConfig:
    min_interval_seconds: float = 2.0
    timeout_seconds: float = 10.0
    max_retries: int = 2
    circuit_break_after_failures: int = 3


@dataclass
class SourceConfig:
    key: str
    company: str
    aliases: list[str]
    domain: str
    entry_url: str
    terms_url: str | None
    source_type: str
    allowed_access: list[str]
    terms_manual_review: str
    status: str
    status_reason: str
    notes: str
    probe_result: dict = field(default_factory=dict)


@dataclass
class SpikeConfig:
    version: int
    user_agent: str
    rate_limit: RateLimitConfig
    sources: list[SourceConfig]
    path: Path

    def source(self, key: str) -> SourceConfig:
        for src in self.sources:
            if src.key == key:
                return src
        raise KeyError(f"来源不存在: {key}")


def load_sources(path: Path | None = None) -> SpikeConfig:
    sources_path = path or (DEFAULT_SPIKE_DIR / "sources.yaml")
    data = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError(f"sources.yaml 格式非法: {sources_path}")
    rl_raw = data.get("rate_limit") or {}
    rate_limit = RateLimitConfig(
        min_interval_seconds=float(rl_raw.get("min_interval_seconds", 2.0)),
        timeout_seconds=float(rl_raw.get("timeout_seconds", 10.0)),
        max_retries=int(rl_raw.get("max_retries", 2)),
        circuit_break_after_failures=int(rl_raw.get("circuit_break_after_failures", 3)),
    )
    if rate_limit.min_interval_seconds < 2.0:
        raise ValueError("合规硬边界：单域名限速不得低于 1 请求/2 秒")
    sources: list[SourceConfig] = []
    seen_keys: set[str] = set()
    for raw in data["sources"]:
        key = str(raw.get("key") or "").strip()
        if not key or key in seen_keys:
            raise ValueError(f"来源 key 缺失或重复: {raw!r}")
        seen_keys.add(key)
        status = str(raw.get("status") or "not_verified")
        if status not in VALID_STATUSES:
            raise ValueError(f"来源 {key} 的 status 非法: {status}")
        sources.append(
            SourceConfig(
                key=key,
                company=str(raw.get("company") or ""),
                aliases=[str(a) for a in raw.get("aliases") or []],
                domain=str(raw.get("domain") or ""),
                entry_url=str(raw.get("entry_url") or ""),
                terms_url=str(raw["terms_url"]) if raw.get("terms_url") else None,
                source_type=str(raw.get("source_type") or "company_site"),
                allowed_access=[str(a) for a in raw.get("allowed_access") or []],
                terms_manual_review=str(
                    raw.get("terms_manual_review") or "pending_owner_confirmation"
                ),
                status=status,
                status_reason=str(raw.get("status_reason") or ""),
                notes=str(raw.get("notes") or ""),
                probe_result=dict(raw.get("probe_result") or {}),
            )
        )
    if not sources:
        raise ValueError("sources.yaml 没有任何来源")
    return SpikeConfig(
        version=int(data.get("version") or 0),
        user_agent=str(data.get("user_agent") or ""),
        rate_limit=rate_limit,
        sources=sources,
        path=sources_path,
    )


def save_sources(config: SpikeConfig) -> None:
    """把 probe 结果写回 sources.yaml（结构化字段，头部注释固定重写）。"""
    payload = {
        "version": config.version,
        "user_agent": config.user_agent,
        "rate_limit": {
            "min_interval_seconds": config.rate_limit.min_interval_seconds,
            "timeout_seconds": config.rate_limit.timeout_seconds,
            "max_retries": config.rate_limit.max_retries,
            "circuit_break_after_failures": config.rate_limit.circuit_break_after_failures,
        },
        "sources": [
            {
                "key": s.key,
                "company": s.company,
                "aliases": s.aliases,
                "domain": s.domain,
                "entry_url": s.entry_url,
                "terms_url": s.terms_url,
                "source_type": s.source_type,
                "allowed_access": s.allowed_access,
                "terms_manual_review": s.terms_manual_review,
                "status": s.status,
                "status_reason": s.status_reason,
                "notes": s.notes,
                "probe_result": s.probe_result,
            }
            for s in config.sources
        ],
    }
    text = _SOURCES_HEADER + yaml.safe_dump(
        payload, allow_unicode=True, sort_keys=False, width=100
    )
    config.path.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class Thresholds:
    status: str
    minimum_observation_days: int
    minimum_sources_per_selected_cell: int
    minimum_active_jobs_per_selected_cell: int
    minimum_median_daily_new_jobs_per_selected_cell: int
    minimum_parse_success_rate: float
    maximum_unknown_publish_time_rate: float
    maximum_single_source_share: float


def load_thresholds(path: Path | None = None) -> Thresholds:
    thresholds_path = path or (DEFAULT_SPIKE_DIR / "thresholds.yaml")
    data = yaml.safe_load(thresholds_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("thresholds"), dict):
        raise ValueError(f"thresholds.yaml 格式非法: {thresholds_path}")
    t = data["thresholds"]
    return Thresholds(
        status=str(data.get("status") or "pending_owner_confirmation"),
        minimum_observation_days=int(t["minimum_observation_days"]),
        minimum_sources_per_selected_cell=int(t["minimum_sources_per_selected_cell"]),
        minimum_active_jobs_per_selected_cell=int(t["minimum_active_jobs_per_selected_cell"]),
        minimum_median_daily_new_jobs_per_selected_cell=int(
            t["minimum_median_daily_new_jobs_per_selected_cell"]
        ),
        minimum_parse_success_rate=float(t["minimum_parse_success_rate"]),
        maximum_unknown_publish_time_rate=float(t["maximum_unknown_publish_time_rate"]),
        maximum_single_source_share=float(t["maximum_single_source_share"]),
    )
