"""定制简历渲染与确定性校验单元测试（阶段 7，离线，无真实依赖）。

覆盖：
- DOCX 产物是合法 OOXML 包（可解包、document.xml 含正文，可重新打开编辑）；
- PDF 产物魔数正确且中文文本层真实可提取（可选择/复制）；
- 长文本折行跨页不截断、空章节有显式占位；
- 校验器拦截：未确认事实引用、编造数字、XX% 占位符、编造岗位原文。
"""

import io
import zipfile

from pypdf import PdfReader

from app.tailoring.render import render_docx, render_pdf, render_resume
from app.tailoring.schemas import (
    ResumeChange,
    ResumeContent,
    ResumeItem,
    ResumeSection,
)
from app.tailoring.validation import fact_value_text, validate_resume_content

FACT_ID = "11111111-1111-1111-1111-111111111111"
FACT_ID_2 = "22222222-2222-2222-2222-222222222222"

ALLOWED = {
    FACT_ID: fact_value_text({"name": "Python", "detail": "FastAPI、asyncio"}),
    FACT_ID_2: fact_value_text(
        {"company": "星辰科技有限公司", "start_raw": "2023.7", "end_raw": "至今"}
    ),
}


def make_content() -> ResumeContent:
    return ResumeContent(
        target_job_title="Python 后端开发工程师",
        target_company="合成测试公司",
        sections=[
            ResumeSection(
                kind="skills",
                title="专业技能",
                items=[ResumeItem(text="Python：FastAPI、asyncio", fact_ids=[FACT_ID])],
            ),
            ResumeSection(
                kind="work_experience",
                title="工作经历",
                items=[
                    ResumeItem(
                        text="星辰科技有限公司（2023.7—至今）", fact_ids=[FACT_ID_2]
                    )
                ],
            ),
            ResumeSection(kind="projects", title="项目经历", items=[]),  # 空章节
        ],
    )


# ---------------- 渲染 ----------------


def test_docx_is_valid_ooxml_package_with_content():
    data = render_docx(make_content())
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None  # 包完整，可重新打开编辑
        xml = zf.read("word/document.xml").decode("utf-8")
    assert "Python：FastAPI、asyncio" in xml
    assert "星辰科技有限公司（2023.7—至今）" in xml
    assert "目标岗位" in xml
    assert "（本节暂无已确认内容）" in xml  # 空章节显式占位，不出现孤立标题


def test_pdf_magic_and_chinese_text_layer_extractable():
    data = render_pdf(make_content())
    assert data.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    # 文本层真实可选择/复制（不是图片）
    assert "星辰科技有限公司" in text
    assert "2023.7" in text


def test_pdf_long_items_wrap_across_pages_without_truncation():
    long_text = "负责后端服务，" * 60  # 远超单行宽度
    content = ResumeContent(
        sections=[
            ResumeSection(
                kind="skills",
                title="专业技能",
                items=[ResumeItem(text=long_text, fact_ids=[FACT_ID])] * 30,
            )
        ]
    )
    data = render_pdf(content)
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) > 1  # 跨页续排
    text = "".join(page.extract_text() or "" for page in reader.pages)
    # 不截断正文（按字符计数：折行会把短语拆到两行，但一个字都不能少）
    assert text.count("负") == 30 * 60
    assert text.count("务") == 30 * 60


def test_render_resume_rejects_unknown_format():
    try:
        render_resume(make_content(), "html")
    except ValueError:
        return
    raise AssertionError("unsupported format must raise")


# ---------------- 确定性校验 ----------------


def _validate(content: ResumeContent, changes=None, **kwargs):
    return validate_resume_content(
        content,
        changes or [],
        allowed_facts=kwargs.get("allowed_facts", ALLOWED),
        job_text=kwargs.get("job_text", "负责 FastAPI 服务开发"),
        allowed_spans=kwargs.get("allowed_spans", set()),
    )


def test_valid_content_passes():
    assert _validate(make_content()) == []


def test_unknown_fact_id_rejected():
    content = ResumeContent(
        sections=[
            ResumeSection(
                kind="skills",
                title="专业技能",
                items=[
                    ResumeItem(
                        text="Kubernetes 专家",
                        fact_ids=["99999999-9999-9999-9999-999999999999"],
                    )
                ],
            )
        ]
    )
    assert any(v.endswith(":unknown_fact_ids") for v in _validate(content))


def test_fabricated_number_rejected():
    content = ResumeContent(
        sections=[
            ResumeSection(
                kind="skills",
                title="专业技能",
                items=[
                    # 事实里没有 "5 年" 这个数字 → 属于夸大/编造
                    ResumeItem(text="Python：5 年经验", fact_ids=[FACT_ID])
                ],
            )
        ]
    )
    assert any(v.endswith(":number_not_in_facts") for v in _validate(content))


def test_placeholder_percent_rejected():
    content = ResumeContent(
        sections=[
            ResumeSection(
                kind="skills",
                title="专业技能",
                items=[ResumeItem(text="性能提升 XX%", fact_ids=[FACT_ID])],
            )
        ]
    )
    assert any(v.endswith(":unconfirmed_placeholder") for v in _validate(content))


def test_fabricated_job_span_in_changes_rejected():
    changes = [
        ResumeChange(
            change_type="reordered",
            section="skills",
            after="Python：FastAPI、asyncio",
            reason="与岗位相关",
            fact_ids=[FACT_ID],
            job_span="岗位原文里根本没有这句话",
        )
    ]
    violations = _validate(make_content(), changes)
    assert any(v.endswith(":fabricated_job_span") for v in violations)


def test_change_span_from_job_text_or_allowed_spans_ok():
    changes = [
        ResumeChange(
            change_type="reordered",
            section="skills",
            after="Python：FastAPI、asyncio",
            reason="与岗位相关",
            fact_ids=[FACT_ID],
            job_span="FastAPI 服务",
        )
    ]
    assert _validate(make_content(), changes) == []
