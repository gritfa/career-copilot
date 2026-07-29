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

    # 哈希 pepper：邀请码/邮箱哈希/IP 哈希使用（token 哈希用纯 SHA-256，熵足够）。
    # 只能通过环境变量提供；默认值仅限本地开发。
    secret_pepper: str = Field(default="dev-only-pepper", validation_alias="SECRET_PEPPER")

    # SMTP（本地开发用 Mailpit）
    smtp_host: str = Field(default="localhost", validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=1025, validation_alias="SMTP_PORT")
    mail_from: str = Field(default="no-reply@careercopilot.local", validation_alias="MAIL_FROM")

    # magic link
    frontend_base_url: str = Field(
        default="http://localhost:3000", validation_alias="FRONTEND_BASE_URL"
    )
    magic_link_ttl_seconds: int = Field(default=900, validation_alias="MAGIC_LINK_TTL_SECONDS")
    magic_link_rate_limit_per_email: int = Field(
        default=3, validation_alias="MAGIC_LINK_RATE_LIMIT_PER_EMAIL"
    )
    magic_link_rate_limit_per_ip: int = Field(
        default=10, validation_alias="MAGIC_LINK_RATE_LIMIT_PER_IP"
    )
    magic_link_rate_window_seconds: int = Field(
        default=900, validation_alias="MAGIC_LINK_RATE_WINDOW_SECONDS"
    )

    # 会话
    session_cookie_name: str = Field(default="cc_session", validation_alias="SESSION_COOKIE_NAME")
    session_ttl_seconds: int = Field(default=2_592_000, validation_alias="SESSION_TTL_SECONDS")
    admin_session_ttl_seconds: int = Field(
        default=43_200, validation_alias="ADMIN_SESSION_TTL_SECONDS"
    )

    # 账号注销恢复期（天）
    account_deletion_grace_days: int = Field(
        default=7, validation_alias="ACCOUNT_DELETION_GRACE_DAYS"
    )

    # ---- 阶段 3：简历上传 / 存储 / 解析 ----
    # 存储后端：local（backend/var/storage）；oss 为留待真实集成的接口
    storage_backend: Literal["local", "oss"] = Field(
        default="local", validation_alias="STORAGE_BACKEND"
    )
    # 本地存储根目录；相对路径相对 backend/ 解析
    storage_dir: str = Field(default="var/storage", validation_alias="STORAGE_DIR")
    resume_max_size_bytes: int = Field(
        default=10 * 1024 * 1024, validation_alias="RESUME_MAX_SIZE_BYTES"
    )
    resume_max_pages: int = Field(default=30, validation_alias="RESUME_MAX_PAGES")
    # DOCX 解压缩炸弹防护：解压总量与压缩比上限
    docx_max_uncompressed_bytes: int = Field(
        default=50 * 1024 * 1024, validation_alias="DOCX_MAX_UNCOMPRESSED_BYTES"
    )
    docx_max_compression_ratio: float = Field(
        default=100.0, validation_alias="DOCX_MAX_COMPRESSION_RATIO"
    )
    upload_session_ttl_seconds: int = Field(
        default=3600, validation_alias="UPLOAD_SESSION_TTL_SECONDS"
    )
    # 解析（文本提取 + 规则抽取）超时保护
    parse_timeout_seconds: float = Field(default=30.0, validation_alias="PARSE_TIMEOUT_SECONDS")
    # Celery：测试可置 eager；任务代码仍是真实 Celery 任务
    celery_task_always_eager: bool = Field(
        default=False, validation_alias="CELERY_TASK_ALWAYS_EAGER"
    )

    # ---- 阶段 5：Embedding / 匹配 ----
    # 阿里云百炼（DashScope）API key；为空时使用确定性合成 Adapter，
    # 能力 aliyun_embedding 保持 not_verified（00-master：不虚标未验证能力）。
    dashscope_api_key: str = Field(default="", validation_alias="DASHSCOPE_API_KEY")
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="DASHSCOPE_BASE_URL",
    )
    embedding_model_id: str = Field(
        default="text-embedding-v3", validation_alias="EMBEDDING_MODEL_ID"
    )
    embedding_timeout_seconds: float = Field(
        default=10.0, validation_alias="EMBEDDING_TIMEOUT_SECONDS"
    )
    embedding_batch_size: int = Field(default=10, validation_alias="EMBEDDING_BATCH_SIZE")
    embedding_max_retries: int = Field(default=2, validation_alias="EMBEDDING_MAX_RETRIES")
    # 向量召回 Top-K 与每方案每日新推荐上限（docs/07）
    recall_top_k: int = Field(default=50, validation_alias="RECALL_TOP_K")
    daily_recommendation_limit: int = Field(
        default=20, validation_alias="DAILY_RECOMMENDATION_LIMIT"
    )

    # ---- 阶段 6：Model Gateway / 单模型标准分析 ----
    # DeepSeek API key：为空时使用确定性合成分析 Adapter，
    # deepseek_generation / standard_analysis 能力保持 not_verified。
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1", validation_alias="DEEPSEEK_BASE_URL"
    )
    # 具体模型 ID 走配置 + allowlist 管理，不写死不可追踪的 latest（docs/07 第 7.2 节）
    deepseek_model_id: str = Field(default="deepseek-chat", validation_alias="DEEPSEEK_MODEL_ID")
    llm_model_allowlist: list[str] = Field(
        default=["deepseek-chat", "synthetic-analysis@1", "synthetic-tailor@1"],
        validation_alias="LLM_MODEL_ALLOWLIST",
    )
    llm_timeout_seconds: float = Field(default=60.0, validation_alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=2, validation_alias="LLM_MAX_RETRIES")
    llm_max_output_tokens: int = Field(default=2048, validation_alias="LLM_MAX_OUTPUT_TOKENS")
    # 每账号每日手动深度分析次数（docs/07 第 8.3 节；自动触发按 ADR-001 D2 推迟）
    analysis_manual_daily_limit: int = Field(
        default=3, validation_alias="ANALYSIS_MANUAL_DAILY_LIMIT"
    )

    # ---- 阶段 7：定制简历 / DOCX-PDF 导出 ----
    # 每用户每日最多创建的新定制版本数（编辑/确认/重新导出不计数，docs/04 第 11 节）
    resume_tailor_daily_limit: int = Field(
        default=3, validation_alias="RESUME_TAILOR_DAILY_LIMIT"
    )
    # 导出文件保留时长（秒）：过期即清理，不留服务器长期副本（docs/03 第 10 节语义）
    resume_export_ttl_seconds: int = Field(
        default=900, validation_alias="RESUME_EXPORT_TTL_SECONDS"
    )

    @property
    def sync_database_url(self) -> str:
        """Alembic 等同步场景使用的 DSN（psycopg 驱动）。"""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    """进程内缓存的配置单例。"""
    return Settings()
