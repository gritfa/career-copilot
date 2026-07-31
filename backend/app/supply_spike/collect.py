"""collect：只对 probe 判 verified 的来源做一次只读采集，产出库存快照。

硬边界：
- status != verified 的来源一律跳过并记录 compliance_block_reason，绝不"顺手试试"。
- 平台类来源（如 BOSS 直聘）在代码层直接拒绝，无论配置怎么写。
- 网络失败如实记 failure_type，绝不用 fixture 数据冒充采集结果。
- 原始响应存 reports/raw/{run_id}/（URL + 抓取时间 + sha256 + 解析版本）。

解析器 v1 只支持 schema.org JSON-LD JobPosting（各大官网若为纯前端渲染则
raw_found=0，如实反映"静态不可解析"）。
"""

import hashlib
import json
import re
from pathlib import Path

from app.jobs.constants import CITY_CODE_BY_NAME, ROLE_FAMILIES
from app.jobs.normalize import normalize_city, normalize_role_family, normalize_title
from app.supply_spike.config import SourceConfig, SpikeConfig
from app.supply_spike.fetch import SpikeFetcher
from app.supply_spike.runs import RUN_SCHEMA_VERSION, new_run_id, raw_dir, utcnow_iso

COLLECT_PARSER_VERSION = "1"

_FORBIDDEN_DOMAINS = ("zhipin.com",)  # BOSS 直聘：绝对红线，代码层拒绝
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _iter_jsonld_jobs(html: str) -> list[dict]:
    """抽取 HTML 里全部 JSON-LD JobPosting 对象（含 @graph 嵌套）。"""
    jobs: list[dict] = []
    for match in _JSONLD_RE.finditer(html):
        try:
            data = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            candidates = graph if isinstance(graph, list) else [node]
            for cand in candidates:
                if isinstance(cand, dict) and cand.get("@type") == "JobPosting":
                    jobs.append(cand)
    return jobs


def _jsonld_city_text(job: dict) -> str | None:
    loc = job.get("jobLocation")
    nodes = loc if isinstance(loc, list) else [loc]
    for node in nodes:
        if not isinstance(node, dict):
            continue
        address = node.get("address")
        if isinstance(address, dict):
            for field in ("addressLocality", "addressRegion"):
                value = address.get(field)
                if isinstance(value, str) and value.strip():
                    return value
        elif isinstance(address, str) and address.strip():
            return address
    return None


def _parse_job(job: dict, source: SourceConfig) -> dict | None:
    """JSON-LD JobPosting → 标准化条目；缺关键字段返回 None（解析失败）。"""
    title = str(job.get("title") or "").strip()
    if not title:
        return None
    employment_raw = job.get("employmentType")
    employment_values = (
        [str(v) for v in employment_raw]
        if isinstance(employment_raw, list)
        else [str(employment_raw or "")]
    )
    is_full_time = any(
        v.upper() == "FULL_TIME" or "全职" in v for v in employment_values
    )
    city_text = _jsonld_city_text(job)
    city_code, city_kind = normalize_city(city_text)
    role_family, _conf = normalize_role_family(title, str(job.get("description") or ""))
    org = job.get("hiringOrganization")
    company = (
        str(org.get("name")).strip()
        if isinstance(org, dict) and org.get("name")
        else source.company
    )
    published_at = str(job.get("datePosted") or "").strip() or None
    url = str(job.get("url") or "").strip() or None
    return {
        "source_key": source.key,
        "company": company,
        "title": title[:255],
        "title_normalized": normalize_title(title),
        "city_text": city_text,
        "city_code": city_code,
        "city_kind": city_kind,
        "role_family": role_family,
        "full_time": is_full_time,
        "published_at": published_at,
        "url": url,
    }


def _item_key(item: dict) -> str:
    basis = "|".join(
        [
            str(item["company"]),
            str(item["title_normalized"]),
            str(item["city_code"] or ""),
            str(item["url"] or ""),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _eligible(item: dict) -> bool:
    """符合条件 = 全职 + 六城之一 + 六方向之一（与产品范围一致）。"""
    return (
        bool(item["full_time"])
        and item["city_code"] in CITY_CODE_BY_NAME.values()
        and item["role_family"] in ROLE_FAMILIES
    )


def collect_source(
    source: SourceConfig,
    fetcher: SpikeFetcher,
    run_id: str,
    spike_dir: Path | None,
    *,
    save_raw: bool = True,
) -> dict:
    """采集单个 verified 来源的入口页（解析器 v1：单页 JSON-LD 库存）。"""
    if any(source.domain.endswith(d) for d in _FORBIDDEN_DOMAINS):
        return {
            "attempted": False,
            "compliance_block_reason": "forbidden_platform_boss：BOSS 直聘绝对红线",
            "raw_found": 0,
            "parsed_ok": 0,
            "items": [],
        }
    if source.status != "verified":
        return {
            "attempted": False,
            "compliance_block_reason": f"source_not_verified：status={source.status}",
            "raw_found": 0,
            "parsed_ok": 0,
            "items": [],
        }
    result = fetcher.get(source.entry_url)
    if not result.ok:
        return {
            "attempted": True,
            "http_status": result.status_code,
            "failure_type": result.failure_type,
            "raw_found": 0,
            "parsed_ok": 0,
            "items": [],
        }
    if save_raw:
        directory = raw_dir(run_id, spike_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{source.key}.html").write_text(result.text, encoding="utf-8")
        (directory / f"{source.key}.meta.json").write_text(
            json.dumps(
                {
                    "source_url": result.url,
                    "final_url": result.final_url,
                    "fetched_at": utcnow_iso(),
                    "content_hash_sha256": result.content_hash,
                    "parser_version": COLLECT_PARSER_VERSION,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    raw_jobs = _iter_jsonld_jobs(result.text)
    items: list[dict] = []
    for job in raw_jobs:
        parsed = _parse_job(job, source)
        if parsed is not None:
            items.append(parsed)
    return {
        "attempted": True,
        "http_status": result.status_code,
        "failure_type": None,
        "content_hash": result.content_hash,
        "raw_found": len(raw_jobs),
        "parsed_ok": len(items),
        "items": items,
    }


def _cells(items: list[dict]) -> dict[str, dict]:
    """6 城 × 6 方向 = 36 格子的库存分布。"""
    cells: dict[str, dict] = {}
    code_to_name = {v: k for k, v in CITY_CODE_BY_NAME.items()}
    for city_code in CITY_CODE_BY_NAME.values():
        for role in ROLE_FAMILIES:
            cell_key = f"{code_to_name[city_code]}×{role}"
            cell_items = [
                i for i in items if i["city_code"] == city_code and i["role_family"] == role
            ]
            cells[cell_key] = {
                "inventory": len(cell_items),
                "sources": sorted({i["source_key"] for i in cell_items}),
            }
    return cells


def run_collect(
    config: SpikeConfig,
    fetcher: SpikeFetcher,
    *,
    data_origin: str,
    only_source: str | None = None,
    previous_inventory: dict[str, dict] | None = None,
    spike_dir: Path | None = None,
    save_raw: bool = True,
) -> dict:
    """采集全部 verified 来源 → 运行记录（含与上次库存对比；首日只有基线）。"""
    started_at = utcnow_iso()
    run_id = new_run_id("collect")
    per_source: dict[str, dict] = {}
    all_items: list[dict] = []
    for source in config.sources:
        if only_source and source.key != only_source:
            continue
        outcome = collect_source(source, fetcher, run_id, spike_dir, save_raw=save_raw)
        items = outcome.pop("items")
        eligible_items = [i for i in items if _eligible(i)]
        outcome["eligible"] = len(eligible_items)
        per_source[source.key] = outcome
        all_items.extend(eligible_items)

    inventory: dict[str, dict] = {}
    for item in all_items:
        inventory.setdefault(_item_key(item), item)  # 去重后库存
    deduped = list(inventory.values())

    prev_keys = set(previous_inventory.keys()) if previous_inventory else set()
    new_keys = [k for k in inventory if k not in prev_keys] if previous_inventory else []
    gone_keys = [k for k in prev_keys if k not in inventory] if previous_inventory else []

    unknown_publish = [i for i in deduped if not i["published_at"]]
    raw_found = sum(s["raw_found"] for s in per_source.values())
    parsed_ok = sum(s["parsed_ok"] for s in per_source.values())
    by_source_counts = {
        key: sum(1 for i in deduped if i["source_key"] == key) for key in per_source
    }
    max_share = (
        max(by_source_counts.values()) / len(deduped) if deduped else None
    )

    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "command": "collect",
        "data_origin": data_origin,
        "parser_version": COLLECT_PARSER_VERSION,
        "started_at": started_at,
        "completed_at": utcnow_iso(),
        "sources": per_source,
        "totals": {
            "raw_found": raw_found,
            "parsed_ok": parsed_ok,
            "eligible": len(all_items),
            "after_dedupe": len(deduped),
            "new_vs_previous": len(new_keys) if previous_inventory else None,
            "disappeared_vs_previous": len(gone_keys) if previous_inventory else None,
            "unknown_publish_time_rate": (
                round(len(unknown_publish) / len(deduped), 4) if deduped else None
            ),
            "parse_success_rate": round(parsed_ok / raw_found, 4) if raw_found else None,
            "max_single_source_share": round(max_share, 4) if max_share is not None else None,
        },
        "distribution": {
            "by_source": by_source_counts,
            "by_city": {
                name: sum(1 for i in deduped if i["city_code"] == code)
                for name, code in CITY_CODE_BY_NAME.items()
            },
            "by_role_family": {
                role: sum(1 for i in deduped if i["role_family"] == role)
                for role in ROLE_FAMILIES
            },
            "cells": _cells(deduped),
        },
        "inventory": inventory,
        "requests_made": len(fetcher.request_log),
    }


def latest_inventory(runs: list[dict], data_origin: str) -> dict[str, dict] | None:
    """最近一次同 data_origin collect 的库存（live 绝不和 fixture 对比）。"""
    for record in reversed(runs):
        if record.get("command") == "collect" and record.get("data_origin") == data_origin:
            inv = record.get("inventory")
            if isinstance(inv, dict):
                return inv
    return None
