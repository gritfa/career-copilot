"""SMTP 邮件发送（本地开发 = Mailpit）。

安全：本模块不打任何包含收件人邮箱或 token 的日志。
"""

from email.message import EmailMessage

import aiosmtplib

from app.core.config import get_settings


async def send_magic_link_email(to_email: str, raw_token: str) -> None:
    """发送 magic link 登录邮件；正文包含验证链接，15 分钟内有效。"""
    settings = get_settings()
    link = f"{settings.frontend_base_url}/auth/verify?token={raw_token}"

    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to_email
    message["Subject"] = "CareerCopilot 登录链接"
    ttl_minutes = settings.magic_link_ttl_seconds // 60
    message.set_content(
        "你好，\n\n"
        f"请点击以下链接登录 CareerCopilot（{ttl_minutes} 分钟内有效，仅可使用一次）：\n\n"
        f"{link}\n\n"
        "如果这不是你本人的操作，请忽略本邮件。\n"
    )

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
    )
