"""probe：只查政策与技术可达性（低频只读，每来源 robots.txt / 条款页 / 入口页各一次）。

判定规则（不确定一律 not_verified，绝不"政策不清默认允许"）：
- robots.txt 明确禁止入口路径 → blocked（robots_disallow）。
- 入口重定向到登录/出现验证码信号 → blocked（requires_login / captcha_wall）。
- 网络不可达 / 超时 / 熔断 → not_verified（unreachable），绝不回退 fixture。
- 机器检查全过，但条款未经负责人人工判读（terms_manual_review != cleared）
  或列表为 JS 渲染无法静态解析 → not_verified。
- verified 需要全部满足：robots 允许、无登录/验证码、静态可解析、条款人工判读通过。
"""

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from app.supply_spike.config import SourceConfig, SpikeConfig
from app.supply_spike.fetch import FetchResult, SpikeFetcher
from app.supply_spike.runs import RUN_SCHEMA_VERSION, new_run_id, utcnow_iso

PROBE_PARSER_VERSION = "1"

# 登录/验证码墙的保守信号（命中即停止，绝不尝试绕过）
_LOGIN_URL_MARKERS = ("passport.", "/login", "/signin", "sso.", "auth.")
_CAPTCHA_MARKERS = ("验证码", "captcha", "geetest", "滑块验证")
# 条款页明确禁止采集的关键词（作为证据记录，最终判读仍归负责人）
_TERMS_PROHIBIT_MARKERS = ("禁止抓取", "禁止爬取", "禁止使用爬虫", "不得抓取", "不得爬取")
# 静态页含岗位结构化数据的信号
_STATIC_JOB_MARKERS = ('"JobPosting"', "'JobPosting'", "__NEXT_DATA__", "window.__INITIAL_STATE__")


def _robots_verdict(robots: FetchResult, user_agent: str, entry_url: str) -> dict:
    """robots.txt 结果 → 判定块（allowed/disallowed/unavailable/no_robots）。"""
    entry_path = urlsplit(entry_url).path or "/"
    if not robots.ok:
        return {
            "status": "unavailable",
            "http_status": robots.status_code,
            "failure_type": robots.failure_type,
            "entry_path_allowed": None,
        }
    if robots.status_code == 404:
        # 无 robots.txt：未禁止（如实记录，不等于许可）
        return {"status": "no_robots", "http_status": 404, "entry_path_allowed": True}
    if robots.status_code != 200:
        return {
            "status": "unavailable",
            "http_status": robots.status_code,
            "entry_path_allowed": None,
        }
    parser = RobotFileParser()
    parser.parse(robots.text.splitlines())
    ua_token = user_agent.split("/")[0] if user_agent else "*"
    allowed = parser.can_fetch(ua_token, entry_url) and parser.can_fetch("*", entry_url)
    return {
        "status": "allowed" if allowed else "disallowed",
        "http_status": 200,
        "entry_path": entry_path,
        "entry_path_allowed": allowed,
        "content_hash": robots.content_hash,
    }


def _entry_verdict(entry: FetchResult) -> dict:
    """入口页结果 → 技术可达性判定块。"""
    if not entry.ok:
        return {
            "status": "unreachable",
            "http_status": entry.status_code,
            "failure_type": entry.failure_type,
        }
    final_lower = entry.final_url.lower()
    text_lower = entry.text.lower()
    requires_login = any(m in final_lower for m in _LOGIN_URL_MARKERS)
    captcha = any(m.lower() in text_lower for m in _CAPTCHA_MARKERS)
    static_parseable = any(m.lower() in text_lower for m in _STATIC_JOB_MARKERS)
    return {
        "status": "reachable",
        "http_status": entry.status_code,
        "final_url": entry.final_url,
        "redirected": entry.final_url.rstrip("/") != entry.url.rstrip("/"),
        "requires_login": requires_login,
        "captcha_signal": captcha,
        "static_parseable": static_parseable,
        "content_hash": entry.content_hash,
    }


def _terms_verdict(source: SourceConfig, fetcher: SpikeFetcher) -> dict:
    if not source.terms_url:
        return {"status": "not_located", "note": "未找到公开条款页 URL，需负责人补充"}
    terms = fetcher.get(source.terms_url)
    if not terms.ok:
        return {
            "status": "unavailable",
            "http_status": terms.status_code,
            "failure_type": terms.failure_type,
        }
    hits = [m for m in _TERMS_PROHIBIT_MARKERS if m in terms.text]
    return {
        "status": "fetched",
        "http_status": terms.status_code,
        "prohibit_keyword_hits": hits,
        "content_hash": terms.content_hash,
        "note": "关键词命中仅为证据线索，许可判读归负责人",
    }


def _decide(
    source: SourceConfig, robots_v: dict, entry_v: dict, terms_v: dict
) -> tuple[str, str]:
    """→ (status, reason)。人工已标 blocked 的来源只可维持，不可放宽。"""
    if source.status == "blocked" and source.probe_result:
        return "blocked", source.status_reason or "人工标记 blocked，不放宽"
    if robots_v["status"] == "disallowed":
        return "blocked", "robots_disallow：robots.txt 禁止入口路径"
    if entry_v["status"] == "unreachable":
        return "not_verified", f"unreachable：入口不可达（{entry_v.get('failure_type')}）"
    if entry_v.get("requires_login"):
        return "blocked", "requires_login：入口重定向到登录，绝不模拟登录"
    if entry_v.get("captcha_signal"):
        return "blocked", "captcha_wall：页面含验证码信号，绝不绕过"
    if terms_v.get("prohibit_keyword_hits"):
        return "blocked", "terms_prohibit_signal：条款页出现禁止采集字样，待负责人判读"
    # not_verified 的原因全部列出，便于负责人一次看清还差哪些前置条件
    reasons: list[str] = []
    if robots_v["status"] == "unavailable":
        reasons.append("robots_unavailable：robots.txt 不可达，政策不清不默认允许")
    if source.terms_manual_review != "cleared":
        reasons.append("terms_pending_owner_confirmation：公开条款未经负责人判读，不自动访问")
    if not entry_v.get("static_parseable"):
        reasons.append(
            "js_rendered：列表为前端渲染，静态抓取拿不到岗位数据；逆向内部接口的许可未确认，不做"
        )
    if reasons:
        return "not_verified", "；".join(reasons)
    return "verified", "机器检查全过且条款判读通过"


def probe_source(source: SourceConfig, fetcher: SpikeFetcher, user_agent: str) -> dict:
    robots_url = f"https://{source.domain}/robots.txt"
    robots_v = _robots_verdict(fetcher.get(robots_url), user_agent, source.entry_url)
    entry_v = _entry_verdict(fetcher.get(source.entry_url))
    terms_v = _terms_verdict(source, fetcher)
    status, reason = _decide(source, robots_v, entry_v, terms_v)
    return {
        "checked_at": utcnow_iso(),
        "parser_version": PROBE_PARSER_VERSION,
        "robots_url": robots_url,
        "robots": robots_v,
        "entry": entry_v,
        "terms": terms_v,
        "terms_manual_review": source.terms_manual_review,
        "status": status,
        "status_reason": reason,
    }


def run_probe(
    config: SpikeConfig,
    fetcher: SpikeFetcher,
    *,
    data_origin: str,
    only_source: str | None = None,
) -> dict:
    """探测全部（或指定）来源，返回运行记录；来源状态由调用方写回 sources.yaml。"""
    started_at = utcnow_iso()
    results: dict[str, dict] = {}
    for source in config.sources:
        if only_source and source.key != only_source:
            continue
        result = probe_source(source, fetcher, config.user_agent)
        results[source.key] = result
        source.probe_result = result
        source.status = result["status"]
        source.status_reason = result["status_reason"]
    statuses = [r["status"] for r in results.values()]
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": new_run_id("probe"),
        "command": "probe",
        "data_origin": data_origin,
        "started_at": started_at,
        "completed_at": utcnow_iso(),
        "user_agent": config.user_agent,
        "sources": results,
        "summary": {
            "probed": len(results),
            "verified": statuses.count("verified"),
            "not_verified": statuses.count("not_verified"),
            "blocked": statuses.count("blocked"),
        },
        "requests_made": len(fetcher.request_log),
    }
