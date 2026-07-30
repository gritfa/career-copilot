"""report：从历史 runs 生成 Markdown + JSON 报告（含 supply_gate 判定）。

诚实性硬规则：
- 观察天数 = collect 运行覆盖的自然日数；< minimum_observation_days 时
  supply_gate=not_verified，reason=insufficient_observation_days，无一例外。
- 第一天只有库存基线，"每日新增/中位数"不足以计算时输出 null，绝不伪造。
- live 与 fixture 运行绝不混算（data_origin 过滤）。
- 门槛不满足时给收窄建议（最好的 1-2 城市 / 2-3 方向），绝不放宽数据。
"""

import json
import statistics
from datetime import UTC, datetime
from pathlib import Path

from app.jobs.constants import CITY_CODE_BY_NAME, ROLE_FAMILIES, ROLE_FAMILY_LABELS
from app.supply_spike.config import DEFAULT_SPIKE_DIR, SpikeConfig, Thresholds
from app.supply_spike.runs import RUN_SCHEMA_VERSION, new_run_id, utcnow_iso


def _run_date(record: dict) -> str:
    return str(record.get("started_at") or "")[:10]


def _cell_key_of_item(item: dict) -> str | None:
    code_to_name = {v: k for k, v in CITY_CODE_BY_NAME.items()}
    city = code_to_name.get(str(item.get("city_code")))
    role = item.get("role_family")
    if city is None or role not in ROLE_FAMILIES:
        return None
    return f"{city}×{role}"


def _daily_new_by_cell(collect_runs: list[dict]) -> dict[str, list[int]]:
    """按自然日取每天最后一次 collect，计算逐日每格子的新增 key 数。"""
    by_day: dict[str, dict] = {}
    for record in collect_runs:
        by_day[_run_date(record)] = record  # 同日多跑取最后一次
    days = sorted(by_day)
    result: dict[str, list[int]] = {}
    for prev_day, day in zip(days, days[1:], strict=False):
        prev_inv = by_day[prev_day].get("inventory") or {}
        cur_inv = by_day[day].get("inventory") or {}
        new_items = [item for key, item in cur_inv.items() if key not in prev_inv]
        per_cell: dict[str, int] = {}
        for item in new_items:
            cell = _cell_key_of_item(item)
            if cell:
                per_cell[cell] = per_cell.get(cell, 0) + 1
        for city in CITY_CODE_BY_NAME:
            for role in ROLE_FAMILIES:
                cell = f"{city}×{role}"
                result.setdefault(cell, []).append(per_cell.get(cell, 0))
    return result


def evaluate_gate(
    collect_runs: list[dict], thresholds: Thresholds
) -> dict:
    """supply_gate 判定：观察天数不足一律 not_verified；足够时逐格子核对门槛。"""
    observation_days = len({_run_date(r) for r in collect_runs})
    if observation_days < thresholds.minimum_observation_days:
        return {
            "supply_gate": "not_verified",
            "reason": "insufficient_observation_days",
            "observation_days": observation_days,
            "required_days": thresholds.minimum_observation_days,
            "passing_cells": [],
        }
    latest = collect_runs[-1]
    cells = (latest.get("distribution") or {}).get("cells") or {}
    daily_new = _daily_new_by_cell(collect_runs)
    passing: list[str] = []
    cell_verdicts: dict[str, dict] = {}
    for cell_key, cell in cells.items():
        new_series = daily_new.get(cell_key, [])
        median_new = statistics.median(new_series) if new_series else None
        checks = {
            "inventory_ok": cell["inventory"]
            >= thresholds.minimum_active_jobs_per_selected_cell,
            "sources_ok": len(cell["sources"])
            >= thresholds.minimum_sources_per_selected_cell,
            "median_daily_new_ok": (
                median_new is not None
                and median_new >= thresholds.minimum_median_daily_new_jobs_per_selected_cell
            ),
        }
        cell_verdicts[cell_key] = {
            **checks,
            "inventory": cell["inventory"],
            "sources": cell["sources"],
            "median_daily_new": median_new,
        }
        if all(checks.values()):
            passing.append(cell_key)
    totals = latest.get("totals") or {}
    quality = {
        "parse_success_rate": totals.get("parse_success_rate"),
        "parse_success_ok": (
            totals.get("parse_success_rate") is not None
            and totals["parse_success_rate"] >= thresholds.minimum_parse_success_rate
        ),
        "unknown_publish_time_rate": totals.get("unknown_publish_time_rate"),
        "unknown_publish_ok": (
            totals.get("unknown_publish_time_rate") is not None
            and totals["unknown_publish_time_rate"]
            <= thresholds.maximum_unknown_publish_time_rate
        ),
        "max_single_source_share": totals.get("max_single_source_share"),
        "source_share_ok": (
            totals.get("max_single_source_share") is not None
            and totals["max_single_source_share"] <= thresholds.maximum_single_source_share
        ),
    }
    gate_ok = bool(passing) and all(
        quality[k] for k in ("parse_success_ok", "unknown_publish_ok", "source_share_ok")
    )
    return {
        "supply_gate": "verified" if gate_ok else "not_verified",
        "reason": None if gate_ok else "thresholds_not_met",
        "observation_days": observation_days,
        "required_days": thresholds.minimum_observation_days,
        "passing_cells": passing,
        "cell_verdicts": cell_verdicts,
        "quality": quality,
    }


def narrowing_suggestion(latest_collect: dict | None) -> dict:
    """36 格子不满足时的收窄建议：按库存挑最好的 1-2 城市 / 2-3 方向。"""
    if latest_collect is None:
        return {
            "note": "尚无任何 verified 来源的采集数据，无法给收窄建议；"
            "下一步应扩充候选来源或由负责人判读条款解锁现有来源",
            "cities": [],
            "role_families": [],
        }
    dist = latest_collect.get("distribution") or {}
    by_city = dist.get("by_city") or {}
    by_role = dist.get("by_role_family") or {}
    top_cities = sorted(by_city.items(), key=lambda kv: kv[1], reverse=True)[:2]
    top_roles = sorted(by_role.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return {
        "note": "基于当前库存分布的收窄建议（数据不足时仅供方向参考，不构成放宽门槛）",
        "cities": [{"city": c, "inventory": n} for c, n in top_cities if n > 0],
        "role_families": [
            {"role_family": r, "label": ROLE_FAMILY_LABELS.get(r, r), "inventory": n}
            for r, n in top_roles
            if n > 0
        ],
    }


def _sources_table_md(config: SpikeConfig) -> list[str]:
    lines = [
        "| 来源 | 公司 | 域名 | 状态 | 原因 | robots | 入口 | 条款 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in config.sources:
        pr = s.probe_result or {}
        robots = (pr.get("robots") or {}).get("status", "未探测")
        entry = (pr.get("entry") or {}).get("status", "未探测")
        terms = (pr.get("terms") or {}).get("status", "未探测")
        lines.append(
            f"| {s.key} | {s.company} | {s.domain} | **{s.status}** "
            f"| {s.status_reason} | {robots} | {entry} | {terms} |"
        )
    return lines


def build_report(
    config: SpikeConfig,
    thresholds: Thresholds,
    all_runs: list[dict],
    *,
    data_origin: str = "live",
) -> dict:
    collect_runs = [
        r
        for r in all_runs
        if r.get("command") == "collect" and r.get("data_origin") == data_origin
    ]
    probe_runs = [
        r
        for r in all_runs
        if r.get("command") == "probe" and r.get("data_origin") == data_origin
    ]
    latest_collect = collect_runs[-1] if collect_runs else None
    gate = evaluate_gate(collect_runs, thresholds) if collect_runs else {
        "supply_gate": "not_verified",
        "reason": "insufficient_observation_days",
        "observation_days": 0,
        "required_days": thresholds.minimum_observation_days,
        "passing_cells": [],
    }
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": new_run_id("report"),
        "command": "report",
        "data_origin": data_origin,
        "generated_at": utcnow_iso(),
        "thresholds_status": thresholds.status,
        "gate": gate,
        "narrowing_suggestion": narrowing_suggestion(latest_collect),
        "latest_collect_totals": (latest_collect or {}).get("totals"),
        "latest_collect_distribution": (latest_collect or {}).get("distribution"),
        "runs_considered": {
            "collect": [r["run_id"] for r in collect_runs],
            "probe": [r["run_id"] for r in probe_runs],
        },
    }


def render_markdown(report: dict, config: SpikeConfig, thresholds: Thresholds) -> str:
    gate = report["gate"]
    lines = [
        "# 岗位供给 Spike 报告",
        "",
        f"- 生成时间：{report['generated_at']}（data_origin={report['data_origin']}）",
        f"- **supply_gate: {gate['supply_gate']}**"
        + (f"（reason={gate['reason']}）" if gate.get("reason") else ""),
        f"- 观察天数：{gate['observation_days']} / 要求 {gate['required_days']} 个自然日",
        f"- 门槛状态：{report['thresholds_status']}（负责人未确认，数值可调；数据口径不放宽）",
        "",
        "## 来源政策与技术状态（probe 实测）",
        "",
        *_sources_table_md(config),
        "",
        "## 采集汇总（最近一次 collect）",
        "",
    ]
    totals = report.get("latest_collect_totals")
    if totals:
        lines += [
            f"- 原始发现：{totals['raw_found']}；解析成功：{totals['parsed_ok']}"
            f"（解析成功率：{totals['parse_success_rate']}）",
            f"- 符合条件（全职×六城×六方向）：{totals['eligible']}；"
            f"去重后库存：{totals['after_dedupe']}",
            f"- 较上次新增：{totals['new_vs_previous']}；失效：{totals['disappeared_vs_previous']}"
            "（首日基线为 null，绝不伪造）",
            f"- 发布时间缺失率：{totals['unknown_publish_time_rate']}；"
            f"来源集中度（单源最大占比）：{totals['max_single_source_share']}",
        ]
    else:
        lines.append(
            "- 无任何 verified 来源可采集：本期没有库存数据（如实报告，不硬凑）。"
        )
    lines += ["", "## 36 格子（城市×方向）库存", ""]
    dist = report.get("latest_collect_distribution")
    if dist and dist.get("cells"):
        lines += [
            "| 格子 | 库存 | 来源数 |",
            "|---|---|---|",
        ]
        for cell_key, cell in dist["cells"].items():
            lines.append(f"| {cell_key} | {cell['inventory']} | {len(cell['sources'])} |")
    else:
        lines.append("（无数据）")
    suggestion = report["narrowing_suggestion"]
    lines += [
        "",
        "## 收窄建议",
        "",
        f"- {suggestion['note']}",
        f"- 城市：{[c['city'] for c in suggestion['cities']] or '（无数据）'}",
        f"- 方向：{[r['label'] for r in suggestion['role_families']] or '（无数据）'}",
        "",
        "## 门槛原始值（pending_owner_confirmation）",
        "",
        f"- minimum_observation_days: {thresholds.minimum_observation_days}",
        f"- minimum_sources_per_selected_cell: {thresholds.minimum_sources_per_selected_cell}",
        "- minimum_active_jobs_per_selected_cell: "
        f"{thresholds.minimum_active_jobs_per_selected_cell}",
        "- minimum_median_daily_new_jobs_per_selected_cell: "
        f"{thresholds.minimum_median_daily_new_jobs_per_selected_cell}",
        f"- minimum_parse_success_rate: {thresholds.minimum_parse_success_rate}",
        "- maximum_unknown_publish_time_rate: "
        f"{thresholds.maximum_unknown_publish_time_rate}",
        f"- maximum_single_source_share: {thresholds.maximum_single_source_share}",
        "",
        f"原始运行记录：{report['runs_considered']}",
        "",
    ]
    return "\n".join(lines)


def write_report(
    report: dict,
    markdown: str,
    spike_dir: Path | None = None,
) -> tuple[Path, Path]:
    directory = (spike_dir or DEFAULT_SPIKE_DIR) / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    json_path = directory / f"report-{stamp}.json"
    md_path = directory / f"report-{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(markdown, encoding="utf-8")
    return md_path, json_path
