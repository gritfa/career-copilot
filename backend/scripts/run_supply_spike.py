"""岗位供给 Spike CLI（ADR-002 硬门槛验证；与生产连接器隔离）。

用法（在 backend/ 目录下）：
    uv run python scripts/run_supply_spike.py probe   [--dry-run] [--source KEY]
    uv run python scripts/run_supply_spike.py collect [--dry-run] [--source KEY]
    uv run python scripts/run_supply_spike.py report

- probe：低频只读检查 robots.txt / 条款页 / 入口页，结果如实写回 sources.yaml。
- collect：只跑 probe 判 verified 的来源（≤1 请求/2 秒，超时/重试/熔断）。
- report：从历史 runs 生成 Markdown + JSON；观察不足 7 个自然日一律
  supply_gate=not_verified（insufficient_observation_days）。
- 真实网络失败如实记录（不回退 fixture）；本 CLI 产生的记录 data_origin=live。
"""

import argparse
import json
import sys
from pathlib import Path

# 允许 scripts/ 直跑：把 backend/ 加进 sys.path
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.supply_spike.collect import latest_inventory, run_collect  # noqa: E402
from app.supply_spike.config import (  # noqa: E402
    DEFAULT_SPIKE_DIR,
    load_sources,
    load_thresholds,
    save_sources,
)
from app.supply_spike.fetch import DomainRateLimiter, SpikeFetcher  # noqa: E402
from app.supply_spike.probe import run_probe  # noqa: E402
from app.supply_spike.report import build_report, render_markdown, write_report  # noqa: E402
from app.supply_spike.runs import load_runs, write_run  # noqa: E402


def _make_fetcher(config) -> SpikeFetcher:
    return SpikeFetcher(
        user_agent=config.user_agent,
        limiter=DomainRateLimiter(config.rate_limit.min_interval_seconds),
        timeout_seconds=config.rate_limit.timeout_seconds,
        max_retries=config.rate_limit.max_retries,
        circuit_break_after_failures=config.rate_limit.circuit_break_after_failures,
    )


def cmd_probe(args: argparse.Namespace) -> int:
    config = load_sources()
    targets = [s for s in config.sources if not args.source or s.key == args.source]
    if not targets:
        print(f"来源不存在: {args.source}", file=sys.stderr)
        return 2
    if args.dry_run:
        print("[dry-run] 将探测以下来源（robots.txt / 条款页 / 入口页各 1 次只读请求）：")
        for s in targets:
            print(f"  - {s.key} ({s.domain}) entry={s.entry_url} terms={s.terms_url or '未知'}")
        return 0
    fetcher = _make_fetcher(config)
    record = run_probe(config, fetcher, data_origin="live", only_source=args.source)
    path = write_run(record)
    save_sources(config)
    print(f"probe 完成：{record['summary']}")
    for key, res in record["sources"].items():
        print(f"  - {key}: {res['status']}（{res['status_reason']}）")
    print(f"运行记录：{path}")
    print(f"来源状态已写回：{config.path}")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    config = load_sources()
    verified = [s for s in config.sources if s.status == "verified"]
    if args.dry_run:
        print(f"[dry-run] verified 来源 {len(verified)} 家：{[s.key for s in verified]}")
        print("[dry-run] 非 verified 来源只记录 compliance_block_reason，不发请求")
        return 0
    fetcher = _make_fetcher(config)
    previous = latest_inventory(load_runs(), data_origin="live")
    record = run_collect(
        config,
        fetcher,
        data_origin="live",
        only_source=args.source,
        previous_inventory=previous,
    )
    path = write_run(record)
    totals = record["totals"]
    print(
        f"collect 完成：verified 来源 {len(verified)} 家；"
        f"原始 {totals['raw_found']}，解析 {totals['parsed_ok']}，"
        f"符合条件 {totals['eligible']}，去重后 {totals['after_dedupe']}"
    )
    if not verified:
        print("注意：当前没有任何 verified 来源，本次为如实的空采集记录（不硬凑数据）。")
    print(f"运行记录：{path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    config = load_sources()
    thresholds = load_thresholds()
    all_runs = load_runs()
    report = build_report(config, thresholds, all_runs, data_origin="live")
    markdown = render_markdown(report, config, thresholds)
    if args.dry_run:
        print(markdown)
        return 0
    md_path, json_path = write_report(report, markdown)
    gate = report["gate"]
    print(f"supply_gate={gate['supply_gate']} reason={gate.get('reason')}")
    print(f"observation_days={gate['observation_days']}/{gate['required_days']}")
    print(f"报告：{md_path}")
    print(f"数据：{json_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="run_supply_spike",
        description="岗位供给 Spike：probe 政策/可达性、collect 采集 verified、report 汇总",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("probe", cmd_probe), ("collect", cmd_collect), ("report", cmd_report)):
        p = sub.add_parser(name)
        p.add_argument("--dry-run", action="store_true", help="不发网络请求、不写文件")
        p.add_argument("--source", help="只处理指定来源 key")
        p.set_defaults(func=fn)
    args = parser.parse_args()
    print(f"[supply-spike] 目录：{DEFAULT_SPIKE_DIR}")
    try:
        return args.func(args)
    except FileExistsError as exc:
        print(f"拒绝覆盖不可变记录：{exc}", file=sys.stderr)
        return 3
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"配置/数据错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
