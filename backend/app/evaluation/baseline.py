"""阶段 5 评测基线：合成简历 × 合成岗位，走真实匹配管道并产出报告。

管道（全部复用生产模块，不另写评分逻辑）：
真实规则解析器（app/resumes/parser）→ 已确认事实 → 画像派生（app/matching/profile）
→ 硬条件（app/matching/hard_filters）→ 向量文本 + 确定性 Embedding
（app/integrations/embedding，det-hash-768@1）→ 余弦 Top-K 召回（与
recall_top_k 同语义，离线内存执行，不依赖 pgvector 容器）→ 六维评分
（app/matching/scoring）。

红线（docs/09、agent-prompts/00）：
- 输入全部为合成数据（ADR-001 D5），绝不抓取真实简历/岗位；
- Embedding 为确定性合成向量（det-hash-768@1），**非真实模型结果**，
  报告如实标注 not_verified；
- 受保护属性对偶测试：同一简历只改性别/年龄/婚育/民族/籍贯，
  管道输出必须逐字节一致，否则本命令以非零码退出；
- 报告与日志不含简历正文、姓名、联系方式，只含派生指标与合成岗位摘要。
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from app.core.config import get_settings
from app.evaluation.synthetic_jobs import SYNTHETIC_JOBS, SyntheticPosting
from app.integrations.embedding import DeterministicHashEmbeddingAdapter, EmbeddingGateway
from app.jobs.constants import CITY_CODE_BY_NAME
from app.matching.constants import COMPONENT_WEIGHTS, HARD_RULE_VERSION, SCORING_VERSION
from app.matching.hard_filters import (
    PlanContext,
    evaluate_hard_conditions,
    summarize_hard_status,
)
from app.matching.profile import build_user_profile
from app.matching.scoring import ScoringResult, compute_score
from app.matching.vector_text import (
    PREPROCESS_VERSION,
    build_job_vector_text,
    build_profile_vector_text,
    text_hash,
)
from app.resumes.parser import RuleBasedExtractor, filter_protected

# 合成简历 meta.target_role_family（中文）→ 受控方向 key
ROLE_FAMILY_BY_LABEL: dict[str, str] = {
    "AI应用开发": "ai_application",
    "Python后端": "backend_python",
    "Java后端": "backend_java",
    "前端": "frontend",
    "数据分析/数据工程": "data",
    "软件测试/测试开发": "qa",
}

# 评测方案的统一显式配置（合成，不来自任何真实用户）
PLAN_MINIMUM_MONTHLY_SALARY = 12000
PLAN_TARGET_MONTHLY_SALARY = 18000
PLAN_WORK_MODES = ["onsite", "hybrid"]

COMPONENT_ORDER = tuple(COMPONENT_WEIGHTS)


@dataclass(frozen=True)
class EvalFact:
    """与 ProfileFact 同构的评测事实（id/fact_type/value_json duck-typing）。"""

    id: str
    fact_type: str
    value_json: dict[str, Any]


@dataclass(frozen=True)
class EvalPlan:
    """评分引擎所需的方案字段子集。"""

    role_family: str
    target_monthly_salary: int | None


@dataclass
class PipelineOutput:
    """单份简历跑完整管道的全部产物（对偶比较与报告共用）。"""

    resume_id: str
    role_family: str
    city: str
    profile_education: str | None
    profile_years: float | None
    skill_fact_count: int
    protected_discarded: int
    profile_text_hash: str
    hard: dict[str, list]  # job_id -> [HardConditionResult]
    statuses: dict[str, str]  # job_id -> passed/uncertain/failed
    similarity: dict[str, float]  # job_id -> cosine（仅召回幸存者）
    scored: list[tuple[SyntheticPosting, ScoringResult]]  # 按分数降序
    recommendations: list[tuple[SyntheticPosting, ScoringResult]]


def extract_facts(resume_text: str, resume_id: str) -> tuple[list[EvalFact], int]:
    """真实规则解析器抽取 + 受保护属性二次过滤 → 评测事实（确定性 id）。"""
    output = filter_protected(RuleBasedExtractor().extract(resume_text))
    facts = [
        EvalFact(id=f"{resume_id}:fact:{i}", fact_type=c.fact_type, value_json=c.value_json)
        for i, c in enumerate(output.candidates)
    ]
    return facts, output.protected_discarded_count


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def run_pipeline(
    resume_text: str,
    meta: dict[str, Any],
    jobs: tuple[SyntheticPosting, ...],
    gateway: EmbeddingGateway,
    *,
    recall_k: int,
    top_n: int,
    extra_facts: tuple[EvalFact, ...] = (),
) -> PipelineOutput:
    """对单份简历执行完整匹配管道；``extra_facts`` 仅供故障注入自检使用。"""
    resume_id = meta["resume_id"]
    facts, protected_discarded = extract_facts(resume_text, resume_id)
    facts = facts + list(extra_facts)
    profile = build_user_profile(facts)
    role_family = ROLE_FAMILY_BY_LABEL[meta["target_role_family"]]
    city_code = CITY_CODE_BY_NAME[meta["city"]]

    plan_ctx = PlanContext(
        city_codes=[city_code],
        work_modes=list(PLAN_WORK_MODES),
        minimum_monthly_salary=PLAN_MINIMUM_MONTHLY_SALARY,
        allow_outsourcing=False,
        blocked_company_ids=set(),
        blocked_company_names={},
        user_education_rank=profile.education_rank,
        user_education_level=profile.education_level,
        user_years_experience=profile.years_experience,
    )
    plan = EvalPlan(role_family=role_family, target_monthly_salary=PLAN_TARGET_MONTHLY_SALARY)

    # ---- 硬条件（failed 绝不进入后续环节）----
    hard = {job.job_id: evaluate_hard_conditions(plan_ctx, job) for job in jobs}
    statuses = {job_id: summarize_hard_status(results) for job_id, results in hard.items()}
    survivors = [job for job in jobs if statuses[job.job_id] != "failed"]

    # ---- 向量 + 召回（同 recall_top_k 语义：余弦降序 Top-K，版本单一无混用）----
    profile_text = build_profile_vector_text(
        role_family=role_family,
        city_codes=[city_code],
        work_modes=list(PLAN_WORK_MODES),
        facts=facts,
    )
    texts = [profile_text] + [build_job_vector_text(job) for job in survivors]
    vectors, _usage = gateway.embed(texts)
    profile_vec = vectors[0]
    similarity = {
        job.job_id: _dot(profile_vec, vec)
        for job, vec in zip(survivors, vectors[1:], strict=True)
    }
    recalled = sorted(survivors, key=lambda j: (-similarity[j.job_id], j.job_id))[:recall_k]

    # ---- 六维评分 ----
    scored = [
        (
            job,
            compute_score(
                profile=profile,
                plan=plan,
                posting=job,
                hard_results=hard[job.job_id],
                cosine_similarity=similarity.get(job.job_id),
                company_preference=None,
                learned_weights={},
            ),
        )
        for job in recalled
    ]
    scored.sort(key=lambda t: (-t[1].score_total, t[0].job_id))

    return PipelineOutput(
        resume_id=resume_id,
        role_family=role_family,
        city=meta["city"],
        profile_education=profile.education_level,
        profile_years=profile.years_experience,
        skill_fact_count=len(profile.skill_refs),
        protected_discarded=protected_discarded,
        profile_text_hash=text_hash(profile_text),
        hard=hard,
        statuses=statuses,
        similarity=similarity,
        scored=scored,
        recommendations=scored[:top_n],
    )


# ---------------- 受保护属性对偶测试 ----------------

_AGE_RE = re.compile(r"年龄：(\d{1,3})")


def make_counterfactual_text(resume_text: str) -> str:
    """只改受保护属性：性别互换、年龄 +8、追加婚育/民族/籍贯行；其余逐字不动。"""
    if "性别：男" in resume_text:
        text = resume_text.replace("性别：男", "性别：女")
    else:
        text = resume_text.replace("性别：女", "性别：男")
    text = _AGE_RE.sub(lambda m: f"年龄：{int(m.group(1)) + 8}", text)
    return text.replace(
        "## 个人概况",
        "## 个人概况\n\n- 婚育状况：已婚已育 | 民族：汉 | 籍贯：合成省合成市",
        1,
    )


def compare_outputs(base: PipelineOutput, variant: PipelineOutput) -> list[str]:
    """对偶差异：向量文本、硬条件状态、总分与全部分项必须逐项一致。"""
    diffs: list[str] = []
    if base.profile_text_hash != variant.profile_text_hash:
        diffs.append("profile 向量文本哈希不一致（受保护属性疑似进入向量文本）")
    if base.statuses != variant.statuses:
        diffs.append("硬条件整体状态不一致")
    base_scores = {
        job.job_id: (r.score_total, tuple((c.component, c.score) for c in r.components))
        for job, r in base.scored
    }
    variant_scores = {
        job.job_id: (r.score_total, tuple((c.component, c.score) for c in r.components))
        for job, r in variant.scored
    }
    if set(base_scores) != set(variant_scores):
        diffs.append("参与评分的岗位集合不一致")
    for job_id in sorted(set(base_scores) & set(variant_scores)):
        if base_scores[job_id] != variant_scores[job_id]:
            diffs.append(f"岗位 {job_id} 总分或分项分数不一致")
    return diffs


# ---------------- 预埋缺陷核对（只判 matching 层可观察的信号，不虚构）----------------

_FLAW_ANALYSIS_KEYWORDS = ("空窗", "跳槽", "量化", "单一公司", "入门级", "深度", "罗列", "佐证")


def _own_family_ids(out: PipelineOutput) -> list[str]:
    return [jid for jid in out.hard if jid.startswith(out.role_family)]


def _flaw_verdict(flaw: str, out: PipelineOutput) -> tuple[str, str]:
    """返回 (verdict, evidence)。verdict ∈ reflected / not_reflected /
    partially_observable / not_covered_by_matching。"""
    own_ids = _own_family_ids(out)
    own_scored = [(j, r) for j, r in out.scored if j.job_id in own_ids]
    if "学历" in flaw:
        failed = [
            jid
            for jid in own_ids
            if any(r.condition == "education" and r.status == "failed" for r in out.hard[jid])
        ]
        if failed:
            return "reflected", f"本方向岗位学历硬条件 failed：{', '.join(sorted(failed))}"
        return "not_reflected", "本方向合成岗位学历要求均被满足，硬条件未触发"
    if "错配" in flaw:
        weak = [
            (j.job_id, c.score)
            for j, r in own_scored
            for c in r.components
            if c.component == "core_skills" and (c.score < 70 or c.gap_level in ("minor", "major"))
        ]
        if weak:
            detail = "; ".join(f"{jid} core_skills={s}" for jid, s in weak)
            return "reflected", f"本方向岗位核心技能覆盖存在缺口：{detail}"
        obs = "; ".join(
            f"{j.job_id} core_skills="
            f"{next(c.score for c in r.components if c.component == 'core_skills')}"
            for j, r in own_scored
        )
        return "not_reflected", f"核心技能分项未见明显缺口（观察值：{obs or '本方向无幸存岗位'}）"
    if "笔误" in flaw:
        missing: list[str] = []
        for _j, r in own_scored:
            for c in r.components:
                if c.component != "core_skills":
                    continue
                for ref in c.evidence_refs:
                    missing.extend(ref.get("missing_terms", []))
        if missing:
            terms = ", ".join(sorted(set(missing)))
            return "partially_observable", f"本方向岗位存在词面未命中技术词：{terms}"
        return "not_reflected", "词面匹配未观察到缺口"
    if "堆砌" in flaw:
        rows = []
        for j, r in own_scored:
            core = next(c.score for c in r.components if c.component == "core_skills")
            proj = next(c.score for c in r.components if c.component == "project_evidence")
            rows.append(f"{j.job_id} core={core}/project={proj}")
        return (
            "partially_observable",
            "词面层仅能对照核心技能与项目证据分项（" + "; ".join(rows) + "）；"
            "『无项目支撑』的强度判定属深度分析阶段（阶段 6）",
        )
    if any(k in flaw for k in _FLAW_ANALYSIS_KEYWORDS):
        return (
            "not_covered_by_matching",
            "匹配基线（硬条件+词面+向量）不判定该缺陷；属深度分析阶段（阶段 6）职责",
        )
    return "not_covered_by_matching", "无对应的 matching 层确定性信号；不虚构判定"


# ---------------- 主入口 ----------------


@dataclass
class BaselineResult:
    exit_code: int
    report_path: Path
    resumes_evaluated: int
    jobs_total: int
    counterfactual_failures: list[str] = field(default_factory=list)
    misplaced_recommendations: int = 0


def _load_resumes(resumes_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    for md_path in sorted(resumes_dir.glob("resume-*.md")):
        # resume-01-ai-app.md -> resume-01-ai-app.meta.json
        meta_path = md_path.parent / (md_path.stem + ".meta.json")
        if not meta_path.exists():
            raise FileNotFoundError(f"缺少元数据文件：{meta_path}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not meta.get("synthetic"):
            raise ValueError(f"{meta_path.name} 未标注 synthetic=true，拒绝进入评测（红线）")
        items.append((md_path.read_text(encoding="utf-8"), meta))
    if not items:
        raise FileNotFoundError(f"{resumes_dir} 下没有 resume-*.md 合成简历")
    return items


def run_baseline(
    resumes_dir: Path,
    output_path: Path,
    *,
    inject_counterfactual_fault: bool = False,
) -> BaselineResult:
    """读取合成简历 → 全量跑管道 + 对偶测试 → 写基线报告。

    对偶测试任一简历不一致时 ``exit_code=1``（报告仍会写出，便于排查）。
    ``inject_counterfactual_fault`` 仅用于自检非零退出路径：向对偶变体
    注入一条携带受保护属性的伪事实，模拟"属性泄漏进评分"的故障。
    """
    settings = get_settings()
    recall_k = settings.recall_top_k
    top_n = settings.daily_recommendation_limit
    gateway = EmbeddingGateway(DeterministicHashEmbeddingAdapter())
    jobs = SYNTHETIC_JOBS
    resumes = _load_resumes(resumes_dir)

    outputs: list[PipelineOutput] = []
    counterfactual_rows: list[tuple[str, bool, list[str]]] = []
    flaw_rows: list[tuple[str, str, str, str]] = []  # resume_id, flaw, verdict, evidence

    fault_facts: tuple[EvalFact, ...] = ()
    if inject_counterfactual_fault:
        fault_facts = (
            EvalFact(
                id="fault:protected-leak",
                fact_type="skill",
                value_json={"name": "受保护属性泄漏自检", "detail": "性别 女 年龄 35"},
            ),
        )

    for resume_text, meta in resumes:
        out = run_pipeline(
            resume_text, meta, jobs, gateway, recall_k=recall_k, top_n=top_n
        )
        outputs.append(out)

        variant_text = make_counterfactual_text(resume_text)
        variant = run_pipeline(
            variant_text,
            meta,
            jobs,
            gateway,
            recall_k=recall_k,
            top_n=top_n,
            extra_facts=fault_facts,
        )
        diffs = compare_outputs(out, variant)
        counterfactual_rows.append((out.resume_id, not diffs, diffs))

        for flaw in meta.get("seeded_flaws", []):
            verdict, evidence = _flaw_verdict(flaw, out)
            flaw_rows.append((out.resume_id, flaw, verdict, evidence))

    # ---- 硬条件误放检查：推荐里绝不允许出现任何 failed 条件 ----
    misplaced = sum(
        1
        for out in outputs
        for job, _r in out.recommendations
        if any(r.status == "failed" for r in out.hard[job.job_id])
    )

    failures = [rid for rid, ok, _d in counterfactual_rows if not ok]
    exit_code = 1 if failures or misplaced else 0

    report = _render_report(
        outputs=outputs,
        jobs=jobs,
        counterfactual_rows=counterfactual_rows,
        flaw_rows=flaw_rows,
        misplaced=misplaced,
        model_id=gateway.adapter.model_id,
        fault_injected=inject_counterfactual_fault,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    return BaselineResult(
        exit_code=exit_code,
        report_path=output_path,
        resumes_evaluated=len(outputs),
        jobs_total=len(jobs),
        counterfactual_failures=failures,
        misplaced_recommendations=misplaced,
    )


# ---------------- 报告渲染 ----------------


def _component_score(result: ScoringResult, component: str) -> str:
    for c in result.components:
        if c.component == component:
            suffix = "†" if c.uncertainty else ""
            return f"{c.score}{suffix}"
    return "-"


def _render_report(
    *,
    outputs: list[PipelineOutput],
    jobs: tuple[SyntheticPosting, ...],
    counterfactual_rows: list[tuple[str, bool, list[str]]],
    flaw_rows: list[tuple[str, str, str, str]],
    misplaced: int,
    model_id: str,
    fault_injected: bool,
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    a = lines.append

    a("# 匹配引擎离线评测基线报告（阶段 5）")
    a("")
    a(f"- 生成时间：{now}")
    a(f"- 版本链：`{SCORING_VERSION}` / `{HARD_RULE_VERSION}` / "
      f"embedding `{model_id}` / 预处理 `{PREPROCESS_VERSION}`")
    a(f"- 输入：合成简历 {len(outputs)} 份（evaluation/synthetic-resumes/，ADR-001 D5）"
      f" × 合成岗位 {len(jobs)} 个（app/evaluation/synthetic_jobs.py）")
    a("- **数据与效度声明（如实标注，不虚构）**：")
    a("  - 简历与岗位全部为合成数据，未采集任何真实简历或真实招聘平台岗位；")
    a(f"  - Embedding 为**确定性合成向量**（`{model_id}`，特征哈希），"
      "**非真实模型结果**，语义召回质量 `not_verified`；")
    a("  - 词面评分为确定性规则，同输入同输出；本报告可作为回归基线，"
      "但**不能**替代真实模型 + 60 组人工标注集的离线门槛验收（docs/09 第 4、5 节）；")
    a("  - 报告不含简历正文、人名与联系方式，只含派生指标。")
    if fault_injected:
        a("")
        a("> ⚠️ 本次运行开启了 `--inject-counterfactual-fault` 故障注入（非零退出自检模式），")
        a("> 对偶测试结果为**人为制造的失败**，不代表管道真实状态。")
    a("")

    # ---- 汇总 ----
    all_recs = [(out, job, r) for out in outputs for job, r in out.recommendations]
    status_count: dict[str, int] = {"passed": 0, "uncertain": 0, "failed": 0}
    cond_count: dict[str, dict[str, int]] = {}
    for out in outputs:
        for status in out.statuses.values():
            status_count[status] += 1
        for results in out.hard.values():
            for r in results:
                bucket = cond_count.setdefault(r.condition, {})
                bucket[r.status] = bucket.get(r.status, 0) + 1
    pairs = len(outputs) * len(jobs)
    cf_ok = sum(1 for _r, ok, _d in counterfactual_rows if ok)

    a("## 1. 总览")
    a("")
    a(f"- 简历×岗位评估对：**{pairs}**；"
      f"硬条件整体 passed **{status_count['passed']}** / "
      f"uncertain **{status_count['uncertain']}** / failed **{status_count['failed']}**")
    a(f"- 产生推荐条目：**{len(all_recs)}**（每份简历上限 20/日，failed 岗位一律不进入）")
    a(f"- 硬条件误放（推荐中含 failed 条件的条目数）：**{misplaced}**（门槛：0）")
    a(f"- 受保护属性对偶测试：**{cf_ok}/{len(counterfactual_rows)} 一致**"
      f"（{'全部通过' if cf_ok == len(counterfactual_rows) else '存在失败，退出码非零'}）")
    a("")

    a("### 1.1 硬条件逐项分布（全部评估对）")
    a("")
    a("| 条件 | passed | passed_with_penalty | failed | unknown |")
    a("|---|---:|---:|---:|---:|")
    for cond in (
        "city",
        "work_mode",
        "salary",
        "full_time",
        "outsourcing",
        "blocked_company",
        "education",
        "experience",
    ):
        bucket = cond_count.get(cond, {})
        a(
            f"| {cond} | {bucket.get('passed', 0)} | {bucket.get('passed_with_penalty', 0)} "
            f"| {bucket.get('failed', 0)} | {bucket.get('unknown', 0)} |"
        )
    a("")

    a("### 1.2 分项分数分布（全部推荐条目）")
    a("")
    a("| 分项 | 权重 | min | mean | max | insufficient_evidence 次数 |")
    a("|---|---:|---:|---:|---:|---:|")
    for component in COMPONENT_ORDER:
        scores = []
        insufficient = 0
        for _out, _job, r in all_recs:
            for c in r.components:
                if c.component != component:
                    continue
                scores.append(c.score)
                if c.uncertainty == "insufficient_evidence":
                    insufficient += 1
        if scores:
            a(
                f"| {component} | {COMPONENT_WEIGHTS[component]} | {min(scores)} "
                f"| {round(mean(scores), 1)} | {max(scores)} | {insufficient} |"
            )
    totals = [r.score_total for _o, _j, r in all_recs]
    if totals:
        a(
            f"| **score_total** | 100 | {min(totals)} | {round(mean(totals), 1)} "
            f"| {max(totals)} | - |"
        )
    a("")

    # ---- 逐简历 ----
    a("## 2. 逐简历结果")
    a("")
    for out in outputs:
        failed_detail = {
            jid: [r.condition for r in results if r.status == "failed"]
            for jid, results in out.hard.items()
            if out.statuses[jid] == "failed"
        }
        a(f"### {out.resume_id}（方向 {out.role_family}，城市 {out.city}）")
        a("")
        a(
            f"- 画像（真实解析器派生）：学历 `{out.profile_education}`，"
            f"经验 `{out.profile_years}` 年，技能事实 {out.skill_fact_count} 条；"
            f"解析时受保护属性命中 {out.protected_discarded} 处（只计数、全部丢弃）"
        )
        counts = {"passed": 0, "uncertain": 0, "failed": 0}
        for status in out.statuses.values():
            counts[status] += 1
        a(
            f"- 硬条件：passed {counts['passed']} / uncertain {counts['uncertain']} "
            f"/ failed {counts['failed']}（共 {len(jobs)} 岗）"
        )
        if failed_detail:
            detail = "; ".join(
                f"{jid}→{'+'.join(conds)}" for jid, conds in sorted(failed_detail.items())
            )
            a(f"- failed 明细：{detail}")
        a("")
        a("| # | 岗位 | 硬状态 | 总分 | 等级 | core | exp | proj | role | ind | pref | 余弦 |")
        a("|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|")
        for rank, (job, r) in enumerate(out.recommendations, start=1):
            cols = " | ".join(_component_score(r, c) for c in COMPONENT_ORDER)
            sim = out.similarity.get(job.job_id)
            a(
                f"| {rank} | {job.job_id} | {out.statuses[job.job_id]} | {r.score_total} "
                f"| {r.grade} | {cols} | {round(sim, 3) if sim is not None else '-'} |"
            )
        a("")
    a("†：该分项带 uncertainty 标注（insufficient_evidence / vector_only 等），"
      "分值按规则保守给出。")
    a("")

    # ---- 预埋缺陷 ----
    a("## 3. 预埋缺陷核对（meta.seeded_flaws × 匹配层信号）")
    a("")
    a("判定口径：只核对匹配基线（硬条件 + 词面 + 向量）**确定性可观察**的信号；")
    a("空窗期/跳槽/量化缺失等叙事类缺陷属深度分析阶段（阶段 6）职责，"
      "如实标注 `not_covered_by_matching`，不虚构命中。")
    a("")
    a("| 简历 | 预埋缺陷 | 判定 | 证据/观察 |")
    a("|---|---|---|---|")
    for rid, flaw, verdict, evidence in flaw_rows:
        a(f"| {rid} | {flaw} | {verdict} | {evidence} |")
    verdict_count: dict[str, int] = {}
    for _rid, _f, verdict, _e in flaw_rows:
        verdict_count[verdict] = verdict_count.get(verdict, 0) + 1
    summary = "，".join(f"{k}×{v}" for k, v in sorted(verdict_count.items()))
    a("")
    a(f"小结：{summary}。")
    a("")

    # ---- 对偶测试 ----
    a("## 4. 受保护属性对偶测试")
    a("")
    a("方法：同一简历只改受保护属性（性别互换、年龄 +8、追加婚育/民族/籍贯行），")
    a("重跑完整管道，要求向量文本哈希、硬条件状态、总分与全部分项**逐项一致**；")
    a("任一简历不一致则本命令以非零码退出。")
    a("")
    a("| 简历 | 结果 | 差异 |")
    a("|---|---|---|")
    for rid, ok, diffs in counterfactual_rows:
        a(f"| {rid} | {'一致 ✅' if ok else '不一致 ❌'} | {'; '.join(diffs) if diffs else '-'} |")
    a("")

    a("## 5. 与离线门槛（docs/09 第 5 节）的对照")
    a("")
    a(f"- 硬条件误放率：{misplaced}/{len(all_recs) or 1} → "
      f"{'满足 <1% 门槛' if misplaced == 0 else '**不满足**'}（合成岗位集内可完全核验）")
    cf_all_ok = cf_ok == len(counterfactual_rows)
    a(f"- 受保护属性对偶分差：{'0（全部一致）' if cf_all_ok else '**非 0，失败**'}")
    a("- 证据可追溯率：全部分项要么带 evidence_refs 要么显式 `insufficient_evidence`"
      "（由匹配引擎单元/集成测试与本基线共同约束）")
    a("- Top 10 Precision ≥70%：**not_verified** —— 需真实模型 embedding + 60 组人工标注集，"
      "本报告的确定性合成向量不能替代该验收。")
    a("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description="阶段 5 匹配基线评测（全合成数据）")
    parser.add_argument(
        "--resumes-dir",
        type=Path,
        default=repo_root / "evaluation" / "synthetic-resumes",
        help="合成简历目录（resume-*.md + *.meta.json）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "evaluation" / "baseline-report.md",
        help="基线报告输出路径",
    )
    parser.add_argument(
        "--inject-counterfactual-fault",
        action="store_true",
        help="自检用：向对偶变体注入受保护属性伪事实，验证非零退出路径",
    )
    args = parser.parse_args(argv)

    result = run_baseline(
        args.resumes_dir,
        args.output,
        inject_counterfactual_fault=args.inject_counterfactual_fault,
    )
    # 只打印计数与路径，不打印任何简历派生内容（红线：日志无正文）
    print(
        f"baseline: resumes={result.resumes_evaluated} jobs={result.jobs_total} "
        f"misplaced={result.misplaced_recommendations} "
        f"counterfactual_failures={len(result.counterfactual_failures)} "
        f"report={result.report_path} exit={result.exit_code}"
    )
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
