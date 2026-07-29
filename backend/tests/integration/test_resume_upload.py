"""上传安全校验：白名单/magic bytes/大小/页数/加密/损坏/前置条件（docs/08 第 5 节）。"""

from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)
from tests.integration.resume_files import (
    make_corrupted_pdf,
    make_docx,
    make_encrypted_pdf,
    make_old_doc,
    make_oversized_pdf,
    make_pdf,
    make_png,
)

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def upload(client, filename: str, data: bytes, content_type: str):
    return await client.post(
        "/api/v1/resumes/uploads", files={"file": (filename, data, content_type)}
    )


async def login(client, db_factory, **user_kwargs):
    user = await create_user(db_factory, unique_email(), **user_kwargs)
    cookie = await create_session_for(db_factory, user)
    client.cookies.set(session_cookie(), cookie)
    return user


async def test_valid_pdf_and_docx_upload_sessions(client, db_factory):
    await login(client, db_factory)

    resp = await upload(client, "resume.pdf", make_pdf(), PDF_MIME)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["media_type"] == PDF_MIME
    assert body["page_count"] == 1
    assert len(body["sha256"]) == 64
    # 恶意扫描引擎未配置：必须如实标注，不伪装 clean
    assert body["malware_scan_status"] == "skipped_not_configured"

    resp = await upload(client, "resume.docx", make_docx(), DOCX_MIME)
    assert resp.status_code == 201, resp.text
    assert resp.json()["media_type"] == DOCX_MIME


async def test_rejects_disguised_oversized_corrupted_encrypted_files(client, db_factory):
    await login(client, db_factory)
    cases = [
        # (文件名, 内容, 声明 MIME, 期望错误码)
        ("fake.pdf", make_docx(), PDF_MIME, "MAGIC_BYTES_MISMATCH"),  # docx 伪装 .pdf
        ("fake.docx", make_pdf(), DOCX_MIME, "MAGIC_BYTES_MISMATCH"),  # pdf 伪装 .docx
        ("evil.exe", b"MZ\x90\x00", "application/octet-stream", "UNSUPPORTED_FILE_TYPE"),
        ("old.doc", make_old_doc(), "application/msword", "UNSUPPORTED_FILE_TYPE"),
        ("scan.png", make_png(), "image/png", "UNSUPPORTED_FILE_TYPE"),
        ("photo.pdf", make_png(), PDF_MIME, "UNSUPPORTED_FILE_TYPE"),  # 图片伪装 .pdf
        ("mismatch.pdf", make_pdf(), "image/png", "MIME_EXTENSION_MISMATCH"),
        ("broken.pdf", make_corrupted_pdf(), PDF_MIME, "CORRUPTED_FILE"),
        ("locked.pdf", make_encrypted_pdf(), PDF_MIME, "ENCRYPTED_PDF_UNSUPPORTED"),
        ("empty.pdf", b"", PDF_MIME, "EMPTY_FILE"),
        ("broken.docx", b"PK\x03\x04" + b"\x00" * 64, DOCX_MIME, "CORRUPTED_FILE"),
    ]
    for filename, data, mime, expected_code in cases:
        resp = await upload(client, filename, data, mime)
        assert resp.status_code in (400, 413, 422), f"{filename}: {resp.status_code}"
        assert resp.json()["error"]["code"] == expected_code, (
            f"{filename}: {resp.json()['error']}"
        )


async def test_rejects_oversized_file(client, db_factory):
    await login(client, db_factory)
    data = make_oversized_pdf(10 * 1024 * 1024 + 1)
    resp = await upload(client, "big.pdf", data, PDF_MIME)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "FILE_TOO_LARGE"


async def test_rejects_too_many_pages(client, db_factory):
    await login(client, db_factory)
    data = make_pdf(lines=["页数测试 page filler"], pages=31)
    resp = await upload(client, "long.pdf", data, PDF_MIME)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "TOO_MANY_PAGES"


async def test_deletion_pending_user_cannot_upload(client, db_factory):
    await login(client, db_factory, status="deletion_pending")
    resp = await upload(client, "resume.pdf", make_pdf(), PDF_MIME)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ACCOUNT_DELETION_PENDING"


async def test_user_without_age_attestation_cannot_upload(client, db_factory):
    from app.core.security import normalize_email
    from app.db.models import User

    async with db_factory() as db:
        user = User(
            email_normalized=normalize_email(unique_email("noage")),
            role="user",
            status="active",
            age_attested_at=None,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    cookie = await create_session_for(db_factory, user)
    client.cookies.set(session_cookie(), cookie)

    resp = await upload(client, "resume.pdf", make_pdf(), PDF_MIME)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AGE_ATTESTATION_REQUIRED"

    # 确认接口同样被前置条件拦截
    resp = await client.post("/api/v1/resumes", json={"upload_id": "a" * 32})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AGE_ATTESTATION_REQUIRED"


async def test_confirm_with_unknown_or_foreign_upload_session(client, db_factory, make_client):
    await login(client, db_factory)
    # 不存在的会话
    resp = await client.post("/api/v1/resumes", json={"upload_id": "f" * 32})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "UPLOAD_SESSION_NOT_FOUND"

    # 他人的会话：一律 404，不泄露存在性
    up = await upload(client, "resume.pdf", make_pdf(), PDF_MIME)
    upload_id = up.json()["upload_id"]
    other = make_client()
    async with other:
        other_user = await create_user(db_factory, unique_email("other"))
        other.cookies.set(session_cookie(), await create_session_for(db_factory, other_user))
        resp = await other.post("/api/v1/resumes", json={"upload_id": upload_id})
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "UPLOAD_SESSION_NOT_FOUND"


async def test_unauthenticated_upload_rejected(client):
    resp = await client.post(
        "/api/v1/resumes/uploads",
        files={"file": ("resume.pdf", make_pdf(), PDF_MIME)},
    )
    assert resp.status_code == 401


async def test_storage_key_contains_no_email_or_filename(client, db_factory):
    """对象 key 只含 UUID：不含邮箱、原文件名（docs/08 第 5 节）。"""
    user = await login(client, db_factory)
    up = await upload(client, "我的个人简历-final.pdf", make_pdf(), PDF_MIME)
    assert up.status_code == 201
    resp = await client.post("/api/v1/resumes", json={"upload_id": up.json()["upload_id"]})
    assert resp.status_code == 202, resp.text

    from sqlalchemy import select

    from app.db.models import Resume

    async with db_factory() as db:
        key = (
            await db.execute(select(Resume.storage_key).where(Resume.user_id == user.id))
        ).scalar_one()
    assert "简历" not in key and "final" not in key and "@" not in key
    assert key.startswith(f"resumes/{user.id}/")
