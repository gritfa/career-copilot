"""Storage Adapter（docs/08 第 5 节）。

约束：
- 对象 key 不包含邮箱、姓名或原文件名，只允许 UUID / 哈希 / 分类前缀。
- 本地实现存 backend/var/storage/（已 gitignore）；OSS 实现留接口，
  真实接入前 storage_backend=oss 会显式失败，不伪装可用。
- 业务模块只依赖 StorageAdapter 协议，不散落具体实现。
"""

import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.core.config import get_settings

_BACKEND_DIR = Path(__file__).resolve().parents[2]

# key 白名单：小写字母/数字/下划线/连字符/斜杠/点，禁止 .. 与绝对路径
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9/_\-.]*$")


class StorageKeyError(ValueError):
    """非法存储 key（含路径穿越、邮箱、明文文件名等风险形态）。"""


def validate_key(key: str) -> str:
    if not _KEY_RE.match(key) or ".." in key or key.endswith("/"):
        raise StorageKeyError(f"invalid storage key shape: {key!r}")
    if "@" in key:
        raise StorageKeyError("storage key must not contain email-like content")
    return key


@runtime_checkable
class StorageAdapter(Protocol):
    """对象存储协议：save/open/delete/exists。"""

    def save(self, key: str, data: bytes) -> None: ...

    def open(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...


class LocalStorageAdapter:
    """本地文件系统实现（开发/测试）。"""

    def __init__(self, root: Path | None = None) -> None:
        settings = get_settings()
        configured = Path(settings.storage_dir)
        if root is not None:
            self.root = root
        elif configured.is_absolute():
            self.root = configured
        else:
            self.root = _BACKEND_DIR / configured

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise StorageKeyError("storage key escapes storage root")
        return path

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def open(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class OSSStorageAdapter:
    """阿里云 OSS 实现留接口：真实 bucket/密钥接入前不可用（not_verified）。

    接入要求（docs/08 第 5 节）：bucket 私有、服务端加密、禁公共 ACL、
    下载用短时签名 URL 且绑定用户权限检查。
    """

    _MSG = "OSS storage is not configured; capability is not_verified"

    def save(self, key: str, data: bytes) -> None:
        raise NotImplementedError(self._MSG)

    def open(self, key: str) -> bytes:
        raise NotImplementedError(self._MSG)

    def delete(self, key: str) -> None:
        raise NotImplementedError(self._MSG)

    def exists(self, key: str) -> bool:
        raise NotImplementedError(self._MSG)


def get_storage() -> StorageAdapter:
    """按配置返回存储实现（业务代码唯一入口）。"""
    if get_settings().storage_backend == "oss":
        return OSSStorageAdapter()
    return LocalStorageAdapter()
