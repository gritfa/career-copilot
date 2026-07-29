"""测试用合成简历文件构造器（代码生成，不提交任何二进制到 git）。

内容改编自 evaluation/synthetic-resumes/resume-03（合成人物，非真实个人简历）。
"""

import io

from docx import Document as DocxDocument
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

_FONT = "STSong-Light"
_font_registered = False


def _ensure_font() -> None:
    global _font_registered
    if not _font_registered:
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT))
        _font_registered = True


# 合成简历文本（含受保护属性行，用于验证丢弃逻辑）
RESUME_LINES = [
    "陈昊然 — Python 后端开发工程师",
    "性别：男 | 年龄：28 | 现居：上海·闵行",
    "民族：汉 | 籍贯：浙江杭州 | 已婚",
    "手机：13912345310 | 邮箱：chenhaoran.py@example.com",
    "求职意向：Python 高级后端开发工程师 | 期望城市：上海",
    "## 教育背景",
    "2017.09 – 2021.06 上海理工大学 计算机科学与技术 本科",
    "## 专业技能",
    "- 语言：Python（精通），熟悉类型标注、asyncio",
    "- Web 框架：FastAPI（主力）、Django / DRF",
    "- 存储：MySQL、PostgreSQL、Redis",
    "## 工作经历",
    "2024.12 – 至今 上海沐医科技有限公司 高级 Python 后端工程师",
    "- 主导处方流转服务重构，接口 P95 延迟从 480ms 降至 160ms",
    "2021.07 – 2024.03 上海鲸帆信息技术有限公司 Python 后端工程师",
    "- 参与订单中台从 Django 单体到服务化拆分",
]

# 隐私扫描断言用：这些字符串绝不允许出现在日志/审计里
SENSITIVE_STRINGS = ("13912345310", "chenhaoran.py@example.com", "陈昊然", "上海沐医科技")


def make_pdf(lines: list[str] | None = None, pages: int = 1) -> bytes:
    """文本型 PDF（真实中文文本层，pypdf 可提取）。"""
    _ensure_font()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for _ in range(pages):
        c.setFont(_FONT, 11)
        y = 800
        for line in lines if lines is not None else RESUME_LINES:
            c.drawString(50, y, line)
            y -= 20
        c.showPage()
    c.save()
    return buf.getvalue()


def make_docx(lines: list[str] | None = None) -> bytes:
    doc = DocxDocument()
    for line in lines if lines is not None else RESUME_LINES:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_blank_pdf(pages: int = 1) -> bytes:
    """无文本层 PDF（模拟扫描件：只有空页/图形，提不出文本）。"""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for _ in range(pages):
        c.rect(100, 100, 300, 500)  # 只画图形，无文本
        c.showPage()
    c.save()
    return buf.getvalue()


def make_encrypted_pdf() -> bytes:
    writer = PdfWriter()
    reader = PdfReader(io.BytesIO(make_pdf()))
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("owner-secret")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_corrupted_pdf() -> bytes:
    return b"%PDF-1.7\n" + b"\x00\xffgarbage-not-a-real-xref" * 40


def make_old_doc() -> bytes:
    """旧 .doc（OLE2 复合文档 magic），不支持。"""
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512


def make_png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def make_oversized_pdf(size_bytes: int) -> bytes:
    """超大文件：合法 PDF 头 + 填充（只用于大小上限校验）。"""
    pad = size_bytes - 9
    return b"%PDF-1.4\n" + b"0" * pad
