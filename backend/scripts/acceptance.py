"""CareerCopilot 阶段 9 端到端验收脚本（可重复执行）。

拓扑（务实方案，与集成测试同级别的真实依赖）：
- 真实 PostgreSQL(pgvector) / Redis / Mailpit（docker compose 提供）；
- 每次运行在同一 PG 实例上重建独立库 ``careercopilot_acceptance`` 并 Alembic 迁移到 head，
  不污染 dev 库、可重复执行；
- 应用经 httpx ASGITransport 进程内调用（同一套 FastAPI 应用工厂 / 中间件 / 错误处理）；
- Celery 走 eager（任务代码即真实 Celery 任务，经 dispatch_task 的 apply 路径同步执行）；
- 邮件走真实 SMTP → Mailpit，脚本从 Mailpit API 取 magic link token（真实收信路径）。

证据边界（如实声明，不虚标）：
- 本脚本验证的是「本地端到端流程」（docs/09 第 1 节层级 4）；
- LLM/Embedding 为确定性合成 Adapter（provider=synthetic），真实模型效果 not_verified；
- 未起独立 uvicorn/celery worker 进程；进程级部署拓扑另由 `make dev` 手册覆盖。

Gate 规则：任何步骤断言失败 → 非零退出；步骤输出只含 ID/计数/状态，不打印简历正文。

用法：
    cd backend && uv run python scripts/acceptance.py
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# ---------------- 环境（必须在导入 app.* 之前确定） ----------------

PG_HOST = os.getenv("ACC_PG_HOST", "localhost")
PG_PORT = int(os.getenv("ACC_PG_PORT", "55432"))
PG_USER = "careercopilot"
PG_PASSWORD = "careercopilot_dev"
ACC_DB = "careercopilot_acceptance"
ACC_DATABASE_URL = f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{ACC_DB}"
ADMIN_SYNC_DSN = (
    f"host={PG_HOST} port={PG_PORT} user={PG_USER} password={PG_PASSWORD} dbname=careercopilot"
)
ACC_SYNC_DSN = (
    f"host={PG_HOST} port={PG_PORT} user={PG_USER} password={PG_PASSWORD} dbname={ACC_DB}"
)
ACC_REDIS_URL = os.getenv("ACC_REDIS_URL", "redis://localhost:56379/2")
MAILPIT_API = os.getenv("ACC_MAILPIT_API", "http://localhost:8025/api/v1")

_STORAGE_TMP = tempfile.mkdtemp(prefix="cc-acceptance-storage-")

os.environ.update(
    {
        "DATABASE_URL": ACC_DATABASE_URL,
        "REDIS_URL": ACC_REDIS_URL,
        "SMTP_HOST": "localhost",
        "SMTP_PORT": os.getenv("ACC_SMTP_PORT", "1025"),
        "ENV": "test",
        "STORAGE_DIR": _STORAGE_TMP,
        "CELERY_TASK_ALWAYS_EAGER": "1",
        # 只让验收步骤输出可读；应用/httpx 的 INFO 请求日志静默（不影响生产配置）
        "LOG_LEVEL": "WARNING",
    }
)

import httpx  # noqa: E402
import psycopg  # noqa: E402
import redis as redis_sync  # noqa: E402

# ---------------- 结果记录 ----------------


class StepFailure(AssertionError):
    pass


RESULTS: list[tuple[str, str, str, float]] = []  # (name, PASS/FAIL, detail, seconds)


def check(cond: bool, message: str) -> None:
    if not cond:
        raise StepFailure(message)


# ---------------- 前置步骤（同步） ----------------


def step_preflight(ctx) -> str:
    """PG / Redis / Mailpit 必须可达，否则直接失败（不伪装通过）。"""
    with psycopg.connect(ADMIN_SYNC_DSN, connect_timeout=5) as conn:
        conn.execute("SELECT 1")
    r = redis_sync.Redis.from_url(ACC_REDIS_URL, socket_connect_timeout=5)
    check(r.ping() is True, "Redis ping 失败")
    r.flushdb()  # 验收专用 db2：清掉上次运行的限流桶等状态
    r.close()
    resp = httpx.get(f"{MAILPIT_API}/messages", timeout=5)
    check(resp.status_code == 200, f"Mailpit API 不可达: {resp.status_code}")
    httpx.delete(f"{MAILPIT_API}/messages", timeout=5)  # 清空收件箱，保证可重复执行
    return f"PostgreSQL:{PG_PORT} / Redis(db2) / Mailpit 可达；Mailpit 已清空"


def step_rebuild_db(ctx) -> str:
    """空库重建 + Alembic 迁移到 head（可重复执行的干净环境）。"""
    with psycopg.connect(ADMIN_SYNC_DSN, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {ACC_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {ACC_DB} OWNER {PG_USER}")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ},
        capture_output=True,
        text=True,
    )
    check(result.returncode == 0, f"alembic upgrade head 失败:\n{result.stderr[-2000:]}")
    with psycopg.connect(ACC_SYNC_DSN) as conn:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    ctx.alembic_head = version
    return f"库 {ACC_DB} 已重建并迁移到 head={version}"


def step_cli_invite(ctx) -> str:
    """真实管理路径：CLI 创建邀请码（库里只存哈希，明文只输出一次）。"""
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "invite", "create", "--max-uses", "5"],
        cwd=BACKEND_DIR,
        env={**os.environ},
        capture_output=True,
        text=True,
    )
    check(result.returncode == 0, f"CLI invite create 失败:\n{result.stderr[-1000:]}")
    # stdout 末尾一行是 JSON（前面可能有审计日志行）
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    check(bool(payload.get("code")), f"CLI 输出缺少邀请码: {payload}")
    ctx.invite_code = payload["code"]
    return f"invite_id={payload['invite_id']} max_uses={payload['max_uses']}"


# ---------------- 用户旅程（异步，httpx ASGI） ----------------


async def step_health(ctx) -> str:
    c = ctx.client
    live = await c.get("/health/live")
    check(live.status_code == 200 and live.json()["status"] == "alive", "live 探针失败")
    ready = await c.get("/health/ready")
    check(ready.status_code == 200, f"ready 探针失败: {ready.text}")
    checks = ready.json()["checks"]
    check(all(item["status"] == "ok" for item in checks.values()), f"ready 检查项异常: {checks}")
    caps = (await c.get("/health/capabilities")).json()["capabilities"]
    # 诚实性 Gate：未真实验证的能力必须仍标 not_verified，不得虚标 ready
    for key in ("deepseek_generation", "qwen_fallback", "aliyun_embedding"):
        check(caps.get(key) == "not_verified", f"能力 {key} 被虚标为 {caps.get(key)}")
    return f"live/ready 通过；capabilities {len(caps)} 项，模型类能力均为 not_verified"


async def step_signup_login(ctx) -> str:
    c = ctx.client
    ctx.email = f"acceptance-{uuid.uuid4().hex[:10]}@cc-acceptance.dev"
    valid = await c.post("/api/v1/auth/invites/validate", json={"code": ctx.invite_code})
    check(valid.status_code == 200 and valid.json()["valid"] is True,
          f"邀请码校验失败: {valid.text}")
    resp = await c.post(
        "/api/v1/auth/magic-links",
        json={"email": ctx.email, "invite_code": ctx.invite_code, "age_attested": True},
    )
    check(resp.status_code == 202, f"magic link 请求失败: {resp.status_code} {resp.text}")

    # 真实收信：从 Mailpit 取 token（轮询最多 5s）
    token = None
    for _ in range(25):
        msgs = httpx.get(f"{MAILPIT_API}/messages", timeout=5).json()["messages"]
        for msg in msgs:
            if ctx.email.lower() in [a["Address"].lower() for a in msg["To"]]:
                body = httpx.get(f"{MAILPIT_API}/message/{msg['ID']}", timeout=5).json()["Text"]
                import re

                match = re.search(r"token=([A-Za-z0-9_-]+)", body)
                check(match is not None, "邮件正文中未找到 token")
                token = match.group(1)
                break
        if token:
            break
        time.sleep(0.2)
    check(token is not None, f"Mailpit 未收到发往 {ctx.email} 的邮件")

    verify = await c.post("/api/v1/auth/magic-links/verify", json={"token": token})
    check(verify.status_code == 200, f"verify 失败: {verify.text}")
    from app.core.config import get_settings

    check(bool(verify.cookies.get(get_settings().session_cookie_name)), "未下发会话 cookie")
    session = await c.get("/api/v1/auth/session")
    check(session.status_code == 200, f"会话查询失败: {session.text}")
    ctx.user_id = session.json()["user"]["id"] if "user" in session.json() else session.json()["id"]
    return f"注册+免密登录闭环通过（Mailpit 真实收信）；user_id={ctx.user_id}"


async def step_consent(ctx) -> str:
    c = ctx.client
    notices = await c.get("/api/v1/consents/notices")
    check(notices.status_code == 200, f"授权告知获取失败: {notices.text}")
    version = notices.json()["providers"]["deepseek"]["notice_version"]
    check(notices.json()["providers"]["deepseek"]["default_checked"] is False, "授权默认勾选违规")
    grant = await c.post(
        "/api/v1/consents",
        json={"provider": "deepseek", "scope": "full_resume", "notice_version": version},
    )
    check(grant.status_code == 201, f"授权失败: {grant.text}")
    return f"deepseek full_resume 授权成功（notice v{version}，默认不勾选已验证）"


async def step_upload_parse(ctx) -> str:
    from tests.integration.resume_files import make_pdf

    c = ctx.client
    ctx.pdf_bytes = make_pdf()
    up = await c.post(
        "/api/v1/resumes/uploads",
        files={"file": ("resume.pdf", ctx.pdf_bytes, "application/pdf")},
    )
    check(up.status_code == 201, f"上传失败: {up.text}")
    check(up.json()["malware_scan_status"] == "skipped_not_configured", "恶意扫描状态虚标")
    confirmed = await c.post("/api/v1/resumes", json={"upload_id": up.json()["upload_id"]})
    check(confirmed.status_code == 202, f"确认上传失败: {confirmed.text}")
    body = confirmed.json()
    check(body["duplicate"] is False, "首次上传被误判为重复")
    check(body["resume"]["status"] == "parsed", f"解析未完成: {body['resume']['status']}")
    ctx.resume_id = body["resume"]["id"]

    parse = await c.get(f"/api/v1/resumes/{ctx.resume_id}/parse")
    check(parse.status_code == 200 and parse.json()["status"] == "succeeded", "解析状态异常")
    pb = parse.json()
    ctx.candidates = pb["candidates"]
    types = {cand["fact_type"] for cand in ctx.candidates}
    need = {"contact_email", "contact_phone", "education", "skill", "work_experience"}
    check(need <= types, f"候选事实类型不全: {types}")
    protected = {"gender", "age", "birth_date", "photo", "marital_status", "ethnicity",
                 "native_place"}
    check(not (types & protected), f"受保护属性进入候选: {types & protected}")
    check(pb["protected_discarded_count"] >= 4, "受保护属性丢弃计数异常")
    return (
        f"PDF 上传→解析闭环通过；候选 {len(ctx.candidates)} 条 / 类型 {len(types)} 种；"
        f"受保护属性丢弃 {pb['protected_discarded_count']} 项且 0 条进入候选"
    )


async def step_confirm_facts(ctx) -> str:
    c = ctx.client
    decisions = []
    rejected = 0
    for cand in ctx.candidates:
        if cand["fact_type"] == "contact_phone" and rejected == 0:
            decisions.append({"candidate_id": cand["id"], "action": "reject"})
            rejected += 1
        else:
            decisions.append({"candidate_id": cand["id"], "action": "accept"})
    resp = await c.post(
        f"/api/v1/resumes/{ctx.resume_id}/facts/confirm", json={"decisions": decisions}
    )
    check(resp.status_code == 200, f"事实确认失败: {resp.text}")
    body = resp.json()
    check(body["accepted"] == len(decisions) - 1 and body["rejected"] == 1,
          f"确认计数异常: {body['accepted']}/{body['rejected']}")
    facts = (await c.get("/api/v1/profile/facts")).json()
    ctx.fact_ids = {f["id"] for f in facts["items"]}
    check(len(ctx.fact_ids) == body["accepted"], "事实库条数与确认数不一致")
    for f in facts["items"]:
        check(f["status"] == "active" and f["confirmed_by_user_at"], "存在未经确认的事实")
    return f"确认 {body['accepted']} 条 / 拒绝 1 条；事实库 {len(ctx.fact_ids)} 条全部经用户确认"


async def step_create_plan(ctx) -> str:
    c = ctx.client
    resp = await c.post(
        "/api/v1/search-plans",
        json={
            "name": "上海 Python 后端（验收）",
            "role_family": "backend_python",
            "city_codes": ["310100"],
            "work_modes": ["onsite", "hybrid"],
            "minimum_match_score": 0,
        },
    )
    check(resp.status_code == 201, f"创建方案失败: {resp.text}")
    ctx.plan_id = resp.json()["id"]
    listing = (await c.get("/api/v1/search-plans")).json()
    check(len(listing["items"]) == 1 and listing["items"][0]["status"] == "active", "方案列表异常")
    return f"求职方案创建成功 plan_id={ctx.plan_id}（backend_python / 上海）"


async def step_import_job(ctx) -> str:
    c = ctx.client
    description = (
        "岗位职责：负责核心业务 FastAPI 服务开发与性能优化，参与 PostgreSQL/Redis 数据层设计，"
        "维护 Celery 异步任务管道。任职要求：3 年以上 Python 后端经验，本科及以上学历，"
        "熟悉 asyncio、类型标注与常见中间件，有高并发服务调优经验者优先。"
    )
    resp = await c.post(
        "/api/v1/jobs/import",
        json={
            "description_text": description,
            "title": "Python 后端开发工程师",
            "company_name": "云帆智联科技（合成岗位）",
            "city": "上海",
            "salary_text": "25-40K·14薪",
            "experience_text": "3-5年",
            "education_text": "本科",
            "employment_text": "全职",
        },
    )
    check(resp.status_code == 201, f"岗位导入失败: {resp.text}")
    body = resp.json()
    check(body["imported_via"] == "text" and body["created"] is True, f"导入结果异常: {body}")
    check(body["canonical_job_id"], "未生成 canonical job")
    ctx.job_id = body["canonical_job_id"]
    return f"用户正文导入岗位成功 canonical_job_id={ctx.job_id}"


async def step_run_matching(ctx) -> str:
    """每日推荐任务（Celery eager：与 beat 调度同一任务代码）。"""
    from app.matching.tasks import generate_recommendations_task

    stats = generate_recommendations_task.apply(args=(str(ctx.plan_id),)).get()
    check(stats.get("status") == "ok", f"匹配任务失败: {stats}")
    check(stats.get("created", 0) >= 1, f"未生成推荐: {stats}")
    ctx.matching_stats = stats
    return f"匹配任务完成: {stats}"


async def step_recommendations(ctx) -> str:
    c = ctx.client
    listing = (await c.get("/api/v1/recommendations")).json()
    check(len(listing["items"]) >= 1, "推荐列表为空")
    ctx.rec_id = listing["items"][0]["id"]
    detail = await c.get(f"/api/v1/recommendations/{ctx.rec_id}")
    check(detail.status_code == 200, f"推荐详情失败: {detail.text}")
    d = detail.json()
    check(d["hard_filter_status"] in ("passed", "uncertain"),
          f"硬过滤状态异常: {d['hard_filter_status']}")
    check(len(d["hard_conditions"]) >= 5, "硬条件项缺失")
    check(len(d["components"]) >= 5, "评分分项缺失")
    with_evidence = sum(1 for comp in d["components"] if comp["evidence_refs"])
    check(with_evidence >= 1, "评分分项无任何证据引用")
    check(d["scoring_version"], "缺少评分版本号")
    return (
        f"推荐 {len(listing['items'])} 条；示例分数 {d['score']}({d['grade']}) "
        f"硬过滤={d['hard_filter_status']}；分项 {len(d['components'])} 个"
        f"（{with_evidence} 个带证据引用）；版本 {d['scoring_version']}"
    )


async def step_deep_analysis(ctx) -> str:
    c = ctx.client
    resp = await c.post(f"/api/v1/recommendations/{ctx.rec_id}/deep-analysis")
    check(resp.status_code == 202, f"触发分析失败: {resp.text}")
    body = resp.json()
    check(body["status"] == "completed", f"分析未完成: {body['status']}")
    check(body["provider"] == "synthetic" and body["verified"] is False,
          "合成分析结果未如实标注 not_verified")
    run = (await c.get(f"/api/v1/agent-runs/{body['id']}")).json()
    check(run["status"] == "completed" and run["report"]["schema_version"] == "std_analysis_v1",
          "分析报告缺失或版本异常")
    check(bool(run["report"]["overall_summary"]), "分析报告缺少总述")
    return (
        f"深度分析完成 run_id={body['id']}（provider=synthetic, verified=false 如实标注；"
        f"报告 schema={run['report']['schema_version']}）"
    )


async def step_tailor_resume(ctx) -> str:
    c = ctx.client
    resp = await c.post(f"/api/v1/recommendations/{ctx.rec_id}/resume-drafts")
    check(resp.status_code == 202, f"生成定制简历失败: {resp.text}")
    body = resp.json()
    check(body["status"] == "draft" and body["kind"] == "job_tailored", "草稿状态异常")
    check(body["verified"] is False, "合成定制结果未标注 not_verified")
    ctx.version_id = body["id"]
    items = [item for sec in body["content"]["sections"] for item in sec["items"]]
    check(len(items) >= 1, "定制简历内容为空")
    for item in items:
        check(bool(item["fact_ids"]), "存在未绑定事实引用的内容行")
        check(set(item["fact_ids"]) <= ctx.fact_ids, "内容引用了未确认事实（虚构风险）")
    check(len(body["changes"]) >= 1, "缺少可追溯的调整记录")
    for change in body["changes"]:
        check(bool(change["reason"]) and set(change["fact_ids"]) <= ctx.fact_ids,
              "调整记录不可追溯")
    return (
        f"定制草稿 version_id={ctx.version_id}；内容 {len(items)} 行 100% 绑定已确认事实；"
        f"调整记录 {len(body['changes'])} 条均可追溯"
    )


async def step_confirm_and_export(ctx) -> str:
    c = ctx.client
    confirm = await c.post(f"/api/v1/resume-versions/{ctx.version_id}/confirm")
    check(confirm.status_code == 200, f"确认版本失败: {confirm.text}")
    sizes = {}
    for fmt, magic in (("docx", b"PK\x03\x04"), ("pdf", b"%PDF")):
        resp = await c.post(
            f"/api/v1/resume-versions/{ctx.version_id}/exports", json={"format": fmt}
        )
        check(resp.status_code == 202, f"{fmt} 导出失败: {resp.text}")
        export = resp.json()
        check(export["status"] == "succeeded", f"{fmt} 导出状态: {export['status']}")
        download = await c.get(f"/api/v1{export['download_url']}")
        check(download.status_code == 200, f"{fmt} 下载失败: {download.status_code}")
        check(download.content.startswith(magic), f"{fmt} 文件头异常")
        sizes[fmt] = len(download.content)
    check(zipfile.is_zipfile(io.BytesIO(await _last_docx(ctx, c))), "DOCX 不是有效 zip 容器")
    return f"确认→导出→限时签名下载闭环通过；DOCX {sizes['docx']}B / PDF {sizes['pdf']}B"


async def _last_docx(ctx, c) -> bytes:
    resp = await c.post(
        f"/api/v1/resume-versions/{ctx.version_id}/exports", json={"format": "docx"}
    )
    check(resp.status_code == 202, "复用 docx 导出失败")
    download = await c.get(f"/api/v1{resp.json()['download_url']}")
    return download.content


async def step_feedback(ctx) -> str:
    c = ctx.client
    resp = await c.post(
        f"/api/v1/recommendations/{ctx.rec_id}/feedback", json={"sentiment": "interested"}
    )
    check(resp.status_code in (200, 201), f"反馈失败: {resp.text}")
    listing = (await c.get("/api/v1/recommendations")).json()
    target = next(i for i in listing["items"] if i["id"] == ctx.rec_id)
    check(target["feedback_sentiment"] == "interested", "反馈未反映在列表")
    return "推荐反馈提交并在列表可见（interested）"


async def step_data_export(ctx) -> str:
    c = ctx.client
    resp = await c.post("/api/v1/privacy/data-exports")
    check(resp.status_code in (201, 202), f"数据导出失败: {resp.text}")
    body = resp.json()
    check(body["status"] == "succeeded", f"导出状态: {body['status']}")
    download = await c.get(f"/api/v1{body['download_url']}")
    check(download.status_code == 200, f"导出下载失败: {download.status_code}")
    check(download.headers["content-type"] == "application/zip", "导出不是 zip")
    zf = zipfile.ZipFile(io.BytesIO(download.content))
    names = zf.namelist()
    check("export.json" in names, f"导出缺少 export.json: {names}")
    payload = json.loads(zf.read("export.json"))
    check(any(e.get("file_in_zip") in names for e in payload.get("resumes", [])),
          "导出缺少简历原始文件")
    # 签名 URL 防篡改：改动签名必须拒绝
    tampered = body["download_url"][:-4] + ("aaaa" if body["download_url"][-4:] != "aaaa"
                                            else "bbbb")
    bad = await c.get(f"/api/v1{tampered}")
    check(bad.status_code in (403, 404), f"篡改签名未被拒绝: {bad.status_code}")
    return f"数据导出 zip {len(download.content)}B / {len(names)} 个文件；篡改签名被拒绝"


async def step_delete_account(ctx) -> str:
    c = ctx.client
    resp = await c.post("/api/v1/privacy/account-deletion")
    check(resp.status_code == 202, f"注销请求失败: {resp.text}")
    # 宽限期内被限制写入
    from tests.integration.resume_files import make_pdf

    blocked = await c.post(
        "/api/v1/resumes/uploads",
        files={"file": ("again.pdf", make_pdf(), "application/pdf")},
    )
    check(blocked.status_code == 403
          and blocked.json()["error"]["code"] == "ACCOUNT_DELETION_PENDING",
          "注销宽限期未阻止上传")

    # 验收加速：把 purge_after 拨到过去（等价于 7 天宽限期到期），再跑真实清理任务
    with psycopg.connect(ACC_SYNC_DSN, autocommit=True) as conn:
        conn.execute(
            "UPDATE users SET purge_after = now() - interval '1 second' WHERE id = %s",
            (ctx.user_id,),
        )
    from app.privacy.tasks import purge_due_accounts_task

    result = purge_due_accounts_task.apply().get()
    check(result.get("succeeded") == 1 and result.get("failed") == 0, f"清理任务失败: {result}")

    with psycopg.connect(ACC_SYNC_DSN) as conn:
        gone = conn.execute("SELECT 1 FROM users WHERE id = %s", (ctx.user_id,)).fetchone()
        check(gone is None, "用户行未被硬删")
        row = conn.execute(
            "SELECT status, manifest_json FROM account_purge_runs WHERE user_id = %s",
            (ctx.user_id,),
        ).fetchone()
    check(row is not None and row[0] == "succeeded", "缺少清理运行记录")
    manifest = row[1]
    check(manifest.get("files_failed") == 0, f"存储文件清理失败: {manifest}")
    session = await c.get("/api/v1/auth/session")
    check(session.status_code == 401, f"注销后会话仍有效: {session.status_code}")
    return (
        f"注销闭环通过：宽限期阻写 → 到期硬删（manifest: resumes={manifest.get('resumes')}, "
        f"plans={manifest.get('search_plans')}, files_failed=0）→ 会话失效"
    )


# ---------------- 运行器 ----------------

SYNC_STEPS = [
    ("基础设施可达（PG/Redis/Mailpit）", step_preflight),
    ("空库重建 + Alembic 迁移 head", step_rebuild_db),
    ("CLI 创建邀请码", step_cli_invite),
]

ASYNC_STEPS = [
    ("健康探针与能力端点（诚实标注）", step_health),
    ("邀请码 + 邮箱免密注册登录（Mailpit 真实收信）", step_signup_login),
    ("模型授权告知与授权（默认不勾选）", step_consent),
    ("简历上传 → 解析 → 候选事实（受保护属性丢弃）", step_upload_parse),
    ("用户确认事实（接受/拒绝，仅确认项入库）", step_confirm_facts),
    ("创建求职方案", step_create_plan),
    ("用户正文导入岗位（标准化管道）", step_import_job),
    ("每日匹配任务生成推荐", step_run_matching),
    ("推荐列表 + 详情（硬条件/分项/证据）", step_recommendations),
    ("触发深度分析（合成模型，如实 not_verified）", step_deep_analysis),
    ("岗位定制简历草稿（100% 事实绑定）", step_tailor_resume),
    ("确认版本 → DOCX/PDF 导出下载", step_confirm_and_export),
    ("推荐反馈", step_feedback),
    ("个人数据导出（zip + 防篡改签名）", step_data_export),
    ("账号注销 → 宽限期 → 硬删清理闭环", step_delete_account),
]


def _record(name: str, status: str, detail: str, seconds: float) -> None:
    RESULTS.append((name, status, detail, seconds))
    idx = len(RESULTS)
    total = len(SYNC_STEPS) + len(ASYNC_STEPS)
    print(f"[{idx:2d}/{total}] {status:4s} {name} ({seconds:.2f}s)")
    print(f"        {detail}")


async def run_async_steps(ctx) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.core.config import get_settings
    from app.core.redis import close_redis
    from app.db.session import dispose_engine
    from app.main import create_app

    get_settings.cache_clear()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://acceptance.local") as client:
        ctx.client = client
        for name, fn in ASYNC_STEPS:
            start = time.monotonic()
            detail = await fn(ctx)
            _record(name, "PASS", detail, time.monotonic() - start)
    await dispose_engine(app)
    await close_redis(app)


def main() -> int:
    import asyncio

    print("=" * 78)
    print("CareerCopilot 阶段 9 端到端验收（真实 PG/pgvector + Redis + Mailpit，Celery eager）")
    print(f"数据库: {ACC_DB}@{PG_HOST}:{PG_PORT}  Redis: {ACC_REDIS_URL}")
    print("=" * 78)
    ctx = SimpleNamespace()
    try:
        for name, fn in SYNC_STEPS:
            start = time.monotonic()
            detail = fn(ctx)
            _record(name, "PASS", detail, time.monotonic() - start)
        asyncio.run(run_async_steps(ctx))
    except BaseException as exc:  # Gate：任何失败必须非零退出
        print("-" * 78)
        print(f"FAIL: {exc}")
        traceback.print_exc()
        passed = sum(1 for r in RESULTS if r[1] == "PASS")
        print(f"结果：{passed} 步通过后失败，验收不通过（退出码 1）")
        return 1
    print("-" * 78)
    print(f"结果：全部 {len(RESULTS)} 步通过。本地端到端用户旅程验收 PASS。")
    print("注意：LLM/Embedding 为合成 Adapter，真实模型效果 not_verified，"
          "详见 docs/acceptance-report.md。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
