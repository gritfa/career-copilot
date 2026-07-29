"""上传文件校验（docs/08 第 5 节）：白名单 + magic bytes + 大小/页数/炸弹防护。

三重校验：扩展名、声明 MIME、文件签名（magic bytes）必须一致且都在白名单。
显式拒绝：旧 .doc（OLE2）、图片、加密 PDF、损坏文件、解压缩炸弹。
错误码稳定（docs/04 1.1 节），信息不回显文件内容。
"""

import io
import zipfile
from dataclasses import dataclass

from pypdf import PdfReader

from app.core.config import get_settings
from app.core.errors import AppError

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

ALLOWED_EXTENSIONS = {".pdf": PDF_MIME, ".docx": DOCX_MIME}

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # 旧 .doc / .xls（不支持）
_IMAGE_MAGICS = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"II*\x00", b"MM\x00*")


@dataclass(frozen=True)
class ValidatedFile:
    """通过全部校验后的文件描述。"""

    media_type: str  # 以服务端嗅探结果为准
    extension: str
    size_bytes: int
    page_count: int | None  # 仅 PDF


def _reject(code: str, message: str, status_code: int = 400) -> AppError:
    return AppError(code=code, message=message, status_code=status_code)


def file_extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def _sniff_kind(data: bytes) -> str:
    """按 magic bytes 嗅探：pdf / zip / ole2 / image / unknown。"""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if data.startswith(_OLE2_MAGIC):
        return "ole2"
    if any(data.startswith(magic) for magic in _IMAGE_MAGICS):
        return "image"
    return "unknown"


def _validate_pdf(data: bytes) -> int:
    """校验 PDF 可解析、未加密、页数达标；返回页数。"""
    settings = get_settings()
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise _reject("ENCRYPTED_PDF_UNSUPPORTED", "不支持加密 PDF，请先解除密码保护")
        page_count = len(reader.pages)
    except AppError:
        raise
    except Exception as exc:
        raise _reject("CORRUPTED_FILE", "文件已损坏或不是有效的 PDF") from exc
    if page_count == 0:
        raise _reject("CORRUPTED_FILE", "PDF 不含任何页面")
    if page_count > settings.resume_max_pages:
        raise _reject(
            "TOO_MANY_PAGES",
            f"页数超过上限（最多 {settings.resume_max_pages} 页）",
        )
    return page_count


def _validate_docx(data: bytes) -> None:
    """校验 DOCX 是合法 OOXML 且非解压缩炸弹。"""
    settings = get_settings()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise _reject("CORRUPTED_FILE", "文件已损坏或不是有效的 DOCX") from exc

    if "[Content_Types].xml" not in names or not any(
        n.startswith("word/") for n in names
    ):
        raise _reject("UNSUPPORTED_FILE_TYPE", "不是有效的 Word (.docx) 文档")

    # 解压缩炸弹防护：条目数、解压总量、压缩比
    if len(infos) > 2000:
        raise _reject("ZIP_BOMB_SUSPECTED", "压缩包条目异常，已拒绝")
    total_uncompressed = sum(i.file_size for i in infos)
    total_compressed = max(sum(i.compress_size for i in infos), 1)
    if total_uncompressed > settings.docx_max_uncompressed_bytes:
        raise _reject("ZIP_BOMB_SUSPECTED", "解压后体积超过安全上限，已拒绝")
    if total_uncompressed / total_compressed > settings.docx_max_compression_ratio:
        raise _reject("ZIP_BOMB_SUSPECTED", "压缩比异常，已拒绝")


def validate_upload(filename: str, declared_media_type: str | None, data: bytes) -> ValidatedFile:
    """对上传文件做全量安全校验；任何一步失败抛出稳定错误码。"""
    settings = get_settings()

    extension = file_extension(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise _reject(
            "UNSUPPORTED_FILE_TYPE",
            "只支持 PDF (.pdf) 和 Word (.docx)；不支持旧版 .doc、图片或扫描件",
        )
    expected_mime = ALLOWED_EXTENSIONS[extension]

    if declared_media_type and declared_media_type not in (
        expected_mime,
        "application/octet-stream",
    ):
        raise _reject("MIME_EXTENSION_MISMATCH", "文件类型与扩展名不一致")

    if len(data) == 0:
        raise _reject("EMPTY_FILE", "文件为空")
    if len(data) > settings.resume_max_size_bytes:
        raise _reject(
            "FILE_TOO_LARGE",
            f"文件超过大小上限（{settings.resume_max_size_bytes // (1024 * 1024)}MB）",
            status_code=413,
        )

    kind = _sniff_kind(data[:16])
    if kind == "ole2":
        raise _reject(
            "UNSUPPORTED_FILE_TYPE", "不支持旧版 .doc，请另存为 .docx 后重新上传"
        )
    if kind == "image":
        raise _reject("UNSUPPORTED_FILE_TYPE", "不支持图片或扫描件，请上传文本型 PDF/DOCX")

    page_count: int | None = None
    if extension == ".pdf":
        if kind != "pdf":
            raise _reject("MAGIC_BYTES_MISMATCH", "文件内容与 .pdf 扩展名不符")
        page_count = _validate_pdf(data)
    else:  # .docx
        if kind != "zip":
            raise _reject("MAGIC_BYTES_MISMATCH", "文件内容与 .docx 扩展名不符")
        _validate_docx(data)

    return ValidatedFile(
        media_type=expected_mime,
        extension=extension,
        size_bytes=len(data),
        page_count=page_count,
    )
