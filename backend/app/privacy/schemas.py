"""隐私模块响应模型（阶段 8：数据导出）。"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class DataExportOut(BaseModel):
    id: uuid.UUID
    status: str
    error_code: str | None
    size_bytes: int | None
    file_sha256: str | None
    # 仅 succeeded 且未过期时返回；限时 HMAC 签名链接
    download_url: str | None
    expires_at: datetime | None
    created_at: datetime
    completed_at: datetime | None
