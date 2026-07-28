"""类型化应用配置：全部从环境变量读取，禁止在代码里硬编码密钥。"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。

    所有值都通过 **大写环境变量** 覆盖（大小写敏感，避免被 shell 里的
    小写同名变量污染），本地开发可用 ``backend/.env``
    （参考 ``.env.example``，不要提交真实 ``.env``）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    app_name: str = Field(default="careercopilot-backend", validation_alias="APP_NAME")
    env: Literal["dev", "test", "staging", "prod"] = Field(default="dev", validation_alias="ENV")
    debug: bool = Field(default=False, validation_alias="DEBUG")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # PostgreSQL + pgvector（生产数据层，禁止 SQLite fallback）
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/careercopilot",
        validation_alias="DATABASE_URL",
    )
    # Redis（Celery broker / 缓存）
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")

    # 健康检查里对外部依赖探测的超时（秒）
    health_check_timeout_seconds: float = Field(
        default=2.0, validation_alias="HEALTH_CHECK_TIMEOUT_SECONDS"
    )

    @property
    def sync_database_url(self) -> str:
        """Alembic 等同步场景使用的 DSN（psycopg 驱动）。"""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    """进程内缓存的配置单例。"""
    return Settings()
