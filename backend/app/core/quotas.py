"""每用户额度覆盖（阶段 8 CLI 管理命令写入 users.quota_overrides_json）。

覆盖键与全局默认的对应关系集中在这里；键不存在 / 非法值一律回退全局默认，
绝不因坏数据把额度放大到无限。
"""

from typing import Any

from app.core.config import get_settings

# 允许覆盖的额度键 → 全局默认的 Settings 属性名
QUOTA_KEYS: dict[str, str] = {
    "analysis_manual_daily": "analysis_manual_daily_limit",
    "resume_tailor_daily": "resume_tailor_daily_limit",
    "data_export_daily": "data_export_daily_limit",
}


def effective_limit(quota_overrides: dict[str, Any] | None, key: str) -> int:
    """返回该用户在 key 上的生效额度（覆盖值优先，缺省用全局配置）。"""
    if key not in QUOTA_KEYS:
        raise KeyError(f"unknown quota key: {key}")
    default = int(getattr(get_settings(), QUOTA_KEYS[key]))
    if not quota_overrides:
        return default
    value = quota_overrides.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return default
