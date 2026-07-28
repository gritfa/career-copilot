"""安全原语：token 生成与哈希、邮箱规范化/哈希、IP 哈希。

原则（docs/08 第 4、8 节）：
- magic link token / 会话 token ≥128 bit 随机，只保存 SHA-256 哈希。
- 邀请码只保存加 pepper 的哈希。
- 日志与审计只允许出现哈希/伪匿名 ID，禁止邮箱明文、token、正文。
"""

import hashlib
import secrets

from app.core.config import get_settings

# secrets.token_urlsafe(32) = 256 bit 随机，远超 128 bit 要求
_TOKEN_BYTES = 32


def generate_token() -> str:
    """生成一次性 token（magic link / 会话），256 bit 随机。"""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def generate_invite_code() -> str:
    """生成邀请码明文（仅在创建时返回一次，库里只存哈希）。"""
    return f"cc-{secrets.token_urlsafe(12)}"


def hash_token(raw: str) -> str:
    """token 哈希：纯 SHA-256（token 本身熵足够，不需要 pepper/慢哈希）。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_invite_code(code: str) -> str:
    """邀请码哈希：SHA-256(pepper + code)，防止低熵码被彩虹表还原。"""
    pepper = get_settings().secret_pepper
    return hashlib.sha256(f"{pepper}:invite:{code}".encode()).hexdigest()


def normalize_email(email: str) -> str:
    """邮箱规范化：去空白 + 全小写。"""
    return email.strip().lower()


def hash_email(email: str) -> str:
    """邮箱哈希（限流桶 / 审计用），加 pepper 防离线枚举。"""
    pepper = get_settings().secret_pepper
    normalized = normalize_email(email)
    return hashlib.sha256(f"{pepper}:email:{normalized}".encode()).hexdigest()


def hash_ip(ip: str | None) -> str:
    """IP 哈希（审计 ip_hash 字段 / 限流桶），加 pepper。"""
    pepper = get_settings().secret_pepper
    return hashlib.sha256(f"{pepper}:ip:{ip or 'unknown'}".encode()).hexdigest()[:32]
