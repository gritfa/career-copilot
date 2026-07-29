"""评测基线主路径测试（小规模合成数据，不依赖 DB/Redis）。

覆盖：run_baseline 主路径（报告生成 + 退出码 0）、受保护属性对偶一致、
故障注入必须非零退出、报告不含联系方式/简历正文、硬条件误放为 0。
"""

import json
from pathlib import Path

import pytest

from app.evaluation.baseline import (
    compare_outputs,
    make_counterfactual_text,
    run_baseline,
)
from app.evaluation.synthetic_jobs import SYNTHETIC_JOBS

REPO_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = REPO_ROOT / "evaluation" / "synthetic-resumes"


@pytest.fixture
def small_resumes_dir(tmp_path: Path) -> Path:
    """从真实评测集抽 2 份（大专×1 + 硕士×1）构成小规模输入。"""
    target = tmp_path / "resumes"
    target.mkdir()
    for stem in ("resume-01-ai-app", "resume-09-qa-test"):
        for suffix in (".md", ".meta.json"):
            src = RESUMES_DIR / f"{stem}{suffix}"
            (target / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_run_baseline_main_path(small_resumes_dir: Path, tmp_path: Path):
    report_path = tmp_path / "report.md"
    result = run_baseline(small_resumes_dir, report_path)

    assert result.exit_code == 0
    assert result.resumes_evaluated == 2
    assert result.jobs_total == len(SYNTHETIC_JOBS)
    assert result.counterfactual_failures == []
    assert result.misplaced_recommendations == 0

    report = report_path.read_text(encoding="utf-8")
    # 关键段落齐全
    assert "受保护属性对偶测试" in report
    assert "预埋缺陷核对" in report
    assert "硬条件逐项分布" in report
    # 红线：如实标注确定性合成向量 not_verified
    assert "not_verified" in report
    assert "det-hash-768@1" in report
    # 大专简历（resume-09）在要求本科的本方向 A 岗上必须体现学历硬条件
    assert "resume-09-qa-test" in report


def test_counterfactual_fault_injection_returns_nonzero(
    small_resumes_dir: Path, tmp_path: Path
):
    result = run_baseline(
        small_resumes_dir,
        tmp_path / "fault-report.md",
        inject_counterfactual_fault=True,
    )
    assert result.exit_code != 0
    assert result.counterfactual_failures  # 每份简历都应被检出不一致


def test_report_contains_no_contact_or_resume_body(small_resumes_dir: Path, tmp_path: Path):
    report_path = tmp_path / "report.md"
    run_baseline(small_resumes_dir, report_path)
    report = report_path.read_text(encoding="utf-8")
    # 合成简历里的联系方式与正文片段绝不进报告（日志/报告无正文红线）
    assert "@example.com" not in report
    assert "138****" not in report
    for stem in ("resume-01-ai-app", "resume-09-qa-test"):
        meta = json.loads((small_resumes_dir / f"{stem}.meta.json").read_text(encoding="utf-8"))
        assert meta["persona_name"] not in report


def test_counterfactual_text_only_touches_protected_attributes():
    original = (
        "# 某某 — 工程师\n\n## 个人概况\n\n- 性别：男 | 年龄：27 | 现居：北京\n\n"
        "## 专业技能\n\n- 语言：Python、FastAPI\n"
    )
    variant = make_counterfactual_text(original)
    assert "性别：女" in variant
    assert "年龄：35" in variant
    assert "婚育状况" in variant
    # 技能等非受保护内容逐字保留
    assert "- 语言：Python、FastAPI" in variant


def test_rejects_dir_without_synthetic_flag(tmp_path: Path):
    (tmp_path / "resume-x.md").write_text("# 简历", encoding="utf-8")
    (tmp_path / "resume-x.meta.json").write_text(
        json.dumps({"synthetic": False, "resume_id": "resume-x"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="synthetic"):
        run_baseline(tmp_path, tmp_path / "r.md")


def test_compare_outputs_detects_score_drift(small_resumes_dir: Path, tmp_path: Path):
    """compare_outputs 对总分/分项漂移敏感（对偶失败即非零退出的判定基础）。"""
    from app.core.config import get_settings
    from app.evaluation.baseline import run_pipeline
    from app.integrations.embedding import (
        DeterministicHashEmbeddingAdapter,
        EmbeddingGateway,
    )

    md = (small_resumes_dir / "resume-01-ai-app.md").read_text(encoding="utf-8")
    meta = json.loads(
        (small_resumes_dir / "resume-01-ai-app.meta.json").read_text(encoding="utf-8")
    )
    gateway = EmbeddingGateway(DeterministicHashEmbeddingAdapter())
    settings = get_settings()
    out = run_pipeline(
        md, meta, SYNTHETIC_JOBS, gateway,
        recall_k=settings.recall_top_k, top_n=settings.daily_recommendation_limit,
    )
    same = run_pipeline(
        md, meta, SYNTHETIC_JOBS, gateway,
        recall_k=settings.recall_top_k, top_n=settings.daily_recommendation_limit,
    )
    assert compare_outputs(out, same) == []  # 确定性：同输入同输出
    # 人为篡改一个分项分数 → 必须检出
    same.scored[0][1].components[0].score += 1
    assert compare_outputs(out, same)
