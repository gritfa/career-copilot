"""供给 Spike 工具离线测试：全部走本地 fixture / MockTransport，不访问公网。

fixture 结果与真实结果用 data_origin: live|fixture 显式区分；
测试同时验证合规硬边界（限速下限、BOSS 拒绝、非 verified 不采集、
网络失败不回退 fixture、运行记录不可覆盖）。
"""

import json
from pathlib import Path

import httpx
import pytest

from app.supply_spike.collect import latest_inventory, run_collect
from app.supply_spike.config import load_sources, load_thresholds
from app.supply_spike.fetch import DomainRateLimiter, SpikeFetcher
from app.supply_spike.probe import run_probe
from app.supply_spike.report import build_report, evaluate_gate, render_markdown
from app.supply_spike.runs import load_runs, new_run_id, write_run

REPO_SPIKE_DIR = Path(__file__).resolve().parents[2] / "supply-spike"

SOURCES_TEMPLATE = """\
version: 1
user_agent: 'CareerCopilotSpike/0.1 (+contact: test@example.invalid)'
rate_limit:
  min_interval_seconds: {interval}
sources:
- key: demo
  company: 演示公司
  aliases: [Demo]
  domain: demo.example
  entry_url: https://demo.example/jobs
  terms_url: https://demo.example/terms
  source_type: company_site
  allowed_access: [robots_txt, terms_page, entry_page_readonly]
  terms_manual_review: {terms_review}
  status: {status}
  status_reason: 测试
  notes: ''
"""

JSONLD_PAGE = """\
<html><body>
<script type="application/ld+json">
[
 {"@type": "JobPosting", "title": "Python 后端开发工程师",
  "employmentType": "FULL_TIME", "datePosted": "2026-07-29",
  "hiringOrganization": {"name": "演示公司"},
  "jobLocation": {"address": {"addressLocality": "北京"}},
  "url": "https://demo.example/jobs/1"},
 {"@type": "JobPosting", "title": "前端开发工程师",
  "employmentType": ["FULL_TIME"],
  "jobLocation": {"address": {"addressLocality": "上海"}},
  "url": "https://demo.example/jobs/2"},
 {"@type": "JobPosting", "title": "测试开发实习生",
  "employmentType": "INTERN",
  "jobLocation": {"address": {"addressLocality": "北京"}},
  "url": "https://demo.example/jobs/3"},
 {"@type": "JobPosting", "title": ""}
]
</script>
<div>__NEXT_DATA__</div>
</body></html>
"""


def make_config(tmp_path, *, status="not_verified", terms_review="pending_owner_confirmation",
                interval=2.0):
    path = tmp_path / "sources.yaml"
    path.write_text(
        SOURCES_TEMPLATE.format(status=status, terms_review=terms_review, interval=interval),
        encoding="utf-8",
    )
    return load_sources(path)


def make_fetcher(handler) -> SpikeFetcher:
    times = iter(range(0, 10_000))
    limiter = DomainRateLimiter(2.0, clock=lambda: float(next(times)) * 10, sleep=lambda _s: None)
    return SpikeFetcher(
        user_agent="CareerCopilotSpike/0.1 (+test)",
        limiter=limiter,
        transport=httpx.MockTransport(handler),
    )


# ---------------- 配置与限速硬边界 ----------------


def test_repo_sources_yaml_loads_with_ten_company_sites():
    config = load_sources(REPO_SPIKE_DIR / "sources.yaml")
    assert len(config.sources) == 10
    assert all(s.source_type == "company_site" for s in config.sources)
    # 红线：清单里绝不允许出现 BOSS 直聘
    assert all("zhipin" not in s.domain for s in config.sources)
    thresholds = load_thresholds(REPO_SPIKE_DIR / "thresholds.yaml")
    assert thresholds.status == "pending_owner_confirmation"
    assert thresholds.minimum_observation_days == 7


def test_rate_limit_floor_is_enforced(tmp_path):
    with pytest.raises(ValueError, match="1 请求/2 秒"):
        make_config(tmp_path, interval=0.5)
    with pytest.raises(ValueError, match="1 请求/2 秒"):
        DomainRateLimiter(1.0)


def test_rate_limiter_spaces_requests_per_domain():
    sleeps: list[float] = []
    now = {"t": 0.0}
    limiter = DomainRateLimiter(2.0, clock=lambda: now["t"], sleep=sleeps.append)
    limiter.wait("a.example")  # 首次不等待
    limiter.wait("a.example")  # 距上次 0s → 需等 2s
    assert sleeps == [2.0]
    now["t"] = 10.0
    limiter.wait("a.example")  # 已隔 10s → 不等待
    assert sleeps == [2.0]


# ---------------- probe 判定 ----------------


def _probe(tmp_path, handler, **kwargs):
    config = make_config(tmp_path, **kwargs)
    fetcher = make_fetcher(handler)
    record = run_probe(config, fetcher, data_origin="fixture")
    assert record["data_origin"] == "fixture"
    return config, record["sources"]["demo"]


def test_probe_robots_disallow_blocks(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /jobs\n")
        return httpx.Response(200, text="<html>jobs</html>")

    config, result = _probe(tmp_path, handler)
    assert result["status"] == "blocked"
    assert "robots_disallow" in result["status_reason"]
    assert config.source("demo").status == "blocked"


def test_probe_login_redirect_blocks(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/jobs":
            return httpx.Response(302, headers={"location": "https://passport.demo.example/login"})
        return httpx.Response(200, text="login page")

    _config, result = _probe(tmp_path, handler)
    assert result["status"] == "blocked"
    assert "requires_login" in result["status_reason"]


def test_probe_captcha_signal_blocks(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="<html>请输入验证码继续访问</html>")

    _config, result = _probe(tmp_path, handler)
    assert result["status"] == "blocked"
    assert "captcha_wall" in result["status_reason"]


def test_probe_network_failure_is_not_verified_never_fixture(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    _config, result = _probe(tmp_path, handler)
    assert result["status"] == "not_verified"
    assert "unreachable" in result["status_reason"]
    # 失败必须如实带 failure_type，绝无伪造成功
    assert result["entry"]["failure_type"] in ("transport_error", "timeout")


def test_probe_terms_pending_keeps_not_verified(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=JSONLD_PAGE)

    _config, result = _probe(tmp_path, handler)
    assert result["status"] == "not_verified"
    assert "terms_pending_owner_confirmation" in result["status_reason"]


def test_probe_all_clear_verifies(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=JSONLD_PAGE)

    config, result = _probe(tmp_path, handler, terms_review="cleared")
    assert result["status"] == "verified"
    assert config.source("demo").status == "verified"


def test_probe_terms_prohibit_keyword_blocks(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/terms":
            return httpx.Response(200, text="本网站禁止抓取任何内容")
        return httpx.Response(200, text=JSONLD_PAGE)

    _config, result = _probe(tmp_path, handler, terms_review="cleared")
    assert result["status"] == "blocked"
    assert "terms_prohibit_signal" in result["status_reason"]


# ---------------- collect 硬边界与解析 ----------------


def test_collect_skips_non_verified_sources_without_requests(tmp_path):
    config = make_config(tmp_path, status="not_verified")
    fetcher = make_fetcher(lambda request: httpx.Response(200, text=JSONLD_PAGE))
    record = run_collect(config, fetcher, data_origin="fixture", spike_dir=tmp_path,
                         save_raw=False)
    assert record["sources"]["demo"]["compliance_block_reason"].startswith(
        "source_not_verified"
    )
    assert fetcher.request_log == []  # 一个请求都没发
    assert record["totals"]["after_dedupe"] == 0


def test_collect_refuses_boss_domain_even_if_marked_verified(tmp_path):
    config = make_config(tmp_path, status="verified")
    config.source("demo").domain = "www.zhipin.com"
    fetcher = make_fetcher(lambda request: httpx.Response(200, text=JSONLD_PAGE))
    record = run_collect(config, fetcher, data_origin="fixture", spike_dir=tmp_path,
                         save_raw=False)
    assert "forbidden_platform_boss" in record["sources"]["demo"]["compliance_block_reason"]
    assert fetcher.request_log == []


def test_collect_parses_jsonld_and_filters_eligibility(tmp_path):
    config = make_config(tmp_path, status="verified")
    fetcher = make_fetcher(lambda request: httpx.Response(200, text=JSONLD_PAGE))
    record = run_collect(config, fetcher, data_origin="fixture", spike_dir=tmp_path)
    totals = record["totals"]
    assert record["data_origin"] == "fixture"
    assert totals["raw_found"] == 4  # 4 个 JobPosting 对象
    assert totals["parsed_ok"] == 3  # 空 title 解析失败
    assert totals["eligible"] == 2  # 实习不算全职
    assert totals["after_dedupe"] == 2
    assert totals["parse_success_rate"] == 0.75
    assert totals["unknown_publish_time_rate"] == 0.5  # 前端岗缺 datePosted
    assert totals["new_vs_previous"] is None  # 首日只有基线，绝不伪造新增
    cells = record["distribution"]["cells"]
    assert cells["北京×backend_python"]["inventory"] == 1
    assert cells["上海×frontend"]["inventory"] == 1
    # 原始证据留存：HTML + 元数据（URL/时间/哈希/解析版本）
    raw_files = list((tmp_path / "reports" / "raw").rglob("demo.meta.json"))
    assert len(raw_files) == 1
    meta = json.loads(raw_files[0].read_text(encoding="utf-8"))
    assert meta["content_hash_sha256"] and meta["parser_version"] == "1"


def test_collect_network_failure_recorded_not_faked(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    config = make_config(tmp_path, status="verified")
    fetcher = make_fetcher(handler)
    record = run_collect(config, fetcher, data_origin="fixture", spike_dir=tmp_path,
                         save_raw=False)
    src = record["sources"]["demo"]
    assert src["attempted"] is True
    assert src["failure_type"] == "transport_error"
    assert record["totals"]["after_dedupe"] == 0  # 失败就是没有数据


def test_collect_second_run_computes_new_and_disappeared(tmp_path):
    config = make_config(tmp_path, status="verified")
    fetcher = make_fetcher(lambda request: httpx.Response(200, text=JSONLD_PAGE))
    first = run_collect(config, fetcher, data_origin="fixture", spike_dir=tmp_path,
                        save_raw=False)
    second = run_collect(
        config,
        make_fetcher(lambda request: httpx.Response(200, text=JSONLD_PAGE)),
        data_origin="fixture",
        spike_dir=tmp_path,
        previous_inventory=first["inventory"],
        save_raw=False,
    )
    assert second["totals"]["new_vs_previous"] == 0
    assert second["totals"]["disappeared_vs_previous"] == 0


def test_latest_inventory_never_mixes_live_and_fixture():
    runs = [
        {"command": "collect", "data_origin": "fixture", "inventory": {"k1": {}}},
        {"command": "collect", "data_origin": "live", "inventory": {"k2": {}}},
    ]
    assert latest_inventory(runs, "live") == {"k2": {}}
    assert latest_inventory(runs, "fixture") == {"k1": {}}


# ---------------- 运行记录不可变 ----------------


def test_write_run_refuses_overwrite(tmp_path):
    record = {"run_id": "20260730T000000Z-collect-abc", "command": "collect"}
    write_run(record, spike_dir=tmp_path)
    with pytest.raises(FileExistsError):
        write_run(record, spike_dir=tmp_path)
    assert len(load_runs(spike_dir=tmp_path)) == 1


# ---------------- report / supply_gate ----------------


def _synthetic_collect_run(day: str, inventory: dict, cells: dict, totals: dict) -> dict:
    return {
        "schema_version": "1",
        "run_id": new_run_id("collect"),
        "command": "collect",
        "data_origin": "fixture",
        "started_at": f"{day}T08:00:00+00:00",
        "inventory": inventory,
        "totals": totals,
        "distribution": {"cells": cells, "by_city": {}, "by_role_family": {}},
    }


def test_gate_not_verified_on_first_day(tmp_path):
    thresholds = load_thresholds(REPO_SPIKE_DIR / "thresholds.yaml")
    config = make_config(tmp_path, status="verified")
    fetcher = make_fetcher(lambda request: httpx.Response(200, text=JSONLD_PAGE))
    run = run_collect(config, fetcher, data_origin="fixture", spike_dir=tmp_path,
                      save_raw=False)
    report = build_report(config, thresholds, [run], data_origin="fixture")
    gate = report["gate"]
    assert gate["supply_gate"] == "not_verified"
    assert gate["reason"] == "insufficient_observation_days"
    assert gate["observation_days"] == 1
    markdown = render_markdown(report, config, thresholds)
    assert "supply_gate: not_verified" in markdown
    assert "pending_owner_confirmation" in markdown


def test_gate_evaluates_cells_after_seven_days():
    thresholds = load_thresholds(REPO_SPIKE_DIR / "thresholds.yaml")

    def item(i: int) -> dict:
        return {"city_code": "110100", "role_family": "backend_python", "published_at": "x",
                "source_key": f"s{i % 3}"}

    runs = []
    inventory: dict[str, dict] = {}
    for day in range(1, 8):  # 7 个自然日，每天新增 5 条
        for i in range(5):
            inventory[f"d{day}-{i}"] = item(i)
        cells = {
            "北京×backend_python": {
                "inventory": len(inventory),
                "sources": ["s0", "s1", "s2"],
            }
        }
        totals = {
            "parse_success_rate": 0.95,
            "unknown_publish_time_rate": 0.1,
            "max_single_source_share": 0.4,
        }
        runs.append(_synthetic_collect_run(f"2026-07-{20 + day:02d}", dict(inventory), cells,
                                           totals))
    gate = evaluate_gate(runs, thresholds)
    assert gate["observation_days"] == 7
    verdict = gate["cell_verdicts"]["北京×backend_python"]
    assert verdict["sources_ok"] and verdict["median_daily_new_ok"]
    assert verdict["inventory_ok"] is False  # 35 < 50：库存门槛不达标，如实
    assert gate["supply_gate"] == "not_verified"
    assert gate["reason"] == "thresholds_not_met"
