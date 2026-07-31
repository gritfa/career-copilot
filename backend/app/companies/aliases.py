"""版本化公司别名配置加载（aliases_v1.yaml）。

- alias 在 company_norm_v1 标准化后建索引；同一 alias 指向两个 canonical 视为配置错误。
- 配置是人工白名单：命中即高置信归一；未命中的相似名走人工审核，绝不自动合并。
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.companies.normalize import normalize_company_name

DEFAULT_ALIASES_PATH = Path(__file__).resolve().parent / "aliases_v1.yaml"


@dataclass(frozen=True)
class AliasConfig:
    version: int
    rules_version: str
    # normalized(alias) -> canonical_name（含 canonical 自身）
    alias_to_canonical: dict[str, str]
    source_path: str


def _load(path: Path) -> AliasConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("companies"), list):
        raise ValueError(f"别名配置格式非法: {path}")
    mapping: dict[str, str] = {}
    for entry in data["companies"]:
        canonical = str(entry.get("canonical_name") or "").strip()
        if not canonical:
            raise ValueError(f"别名配置存在空 canonical_name: {path}")
        names = [canonical, *(str(a) for a in entry.get("aliases") or [])]
        for name in names:
            norm = normalize_company_name(name)
            if norm is None:
                raise ValueError(f"别名配置存在非法名称 {name!r}（canonical={canonical}）")
            existing = mapping.get(norm.normalized)
            if existing is not None and existing != canonical:
                raise ValueError(
                    f"别名冲突：{name!r} 同时指向 {existing!r} 和 {canonical!r}"
                )
            mapping[norm.normalized] = canonical
    return AliasConfig(
        version=int(data.get("version") or 0),
        rules_version=str(data.get("rules_version") or ""),
        alias_to_canonical=mapping,
        source_path=str(path),
    )


@lru_cache(maxsize=4)
def load_alias_config(path: str | None = None) -> AliasConfig:
    """加载并缓存别名配置；path=None 用仓库内 v1 文件。"""
    return _load(Path(path) if path else DEFAULT_ALIASES_PATH)
