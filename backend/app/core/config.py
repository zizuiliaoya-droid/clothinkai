"""应用配置（Pydantic Settings）。

所有配置通过环境变量注入，本模块负责类型校验与默认值。
对应 NFR Requirements / Infrastructure Design 第 6.1 节的环境变量清单。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # 环境标识
    # ------------------------------------------------------------------ #
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"

    # ------------------------------------------------------------------ #
    # Database
    # ------------------------------------------------------------------ #
    DATABASE_URL_APP: str = Field(
        ...,
        description="应用主连接（启用 RLS 角色 clothing_app，asyncpg 驱动）",
    )
    DATABASE_URL_BYPASS: str = Field(
        ...,
        description="系统任务连接（绕过 RLS 角色 clothing_bypass，asyncpg 驱动）",
    )
    DATABASE_URL_SYNC: str = Field(
        ...,
        description="同步连接（pg_dump 备份脚本用，psycopg2 驱动）",
    )
    DATABASE_URL_ARCHIVER: str | None = Field(
        default=None,
        description="audit_log 归档专用连接（asyncpg + clothing_archiver 角色）",
    )
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_ECHO: bool = False

    # ------------------------------------------------------------------ #
    # Redis
    # ------------------------------------------------------------------ #
    REDIS_URL_CACHE: str = Field(..., description="Redis db=0：应用缓存")
    REDIS_URL_CELERY_BROKER: str = Field(..., description="Redis db=1：Celery broker")
    REDIS_URL_CELERY_BACKEND: str = Field(..., description="Redis db=2：Celery backend")

    # ------------------------------------------------------------------ #
    # JWT
    # ------------------------------------------------------------------ #
    JWT_SECRET: SecretStr = Field(..., description="JWT 签名密钥（256 位 hex）")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ------------------------------------------------------------------ #
    # 凭据加密（U12 启用，U01 占位）
    # ------------------------------------------------------------------ #
    CREDENTIAL_MASTER_KEY: SecretStr = Field(
        ...,
        description="AES-256 master key（base64-encoded 32 bytes）",
    )

    # ------------------------------------------------------------------ #
    # 密码策略
    # ------------------------------------------------------------------ #
    BCRYPT_ROUNDS: int = 12

    # ------------------------------------------------------------------ #
    # CORS
    # ------------------------------------------------------------------ #
    CORS_ALLOWED_ORIGINS: str = Field(
        default="http://localhost:5173",
        description="允许的 CORS 源，逗号分隔（如 https://a.com,https://b.com）",
    )

    @property
    def cors_allowed_origins(self) -> list[str]:
        """解析逗号分隔的 CORS 源为 list。

        以 ``str`` 存储（不用 ``list[str]``）以规避 pydantic-settings v2 对复杂类型的
        JSON 预解析 —— 否则 ``http://localhost:5173`` 会被当作非法 JSON 抛
        SettingsError。app 启动时用本属性读取。
        """
        return [o.strip() for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

    # ------------------------------------------------------------------ #
    # Cloudflare R2
    # ------------------------------------------------------------------ #
    R2_ENDPOINT_URL: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: SecretStr | None = None
    R2_BUCKET_PUBLIC: str = "clothing-erp-public"
    R2_BUCKET_PRIVATE: str = "clothing-erp-private"
    R2_BUCKET_CREDENTIALS: str = "clothing-erp-credentials"
    R2_BUCKET_BACKUPS: str = "clothing-erp-backups"
    R2_PUBLIC_BASE_URL: str | None = Field(
        default=None,
        description="R2 公开桶的 CDN 域名前缀（用于生成 public URL）",
    )

    # ------------------------------------------------------------------ #
    # Sentry
    # ------------------------------------------------------------------ #
    SENTRY_DSN_BACKEND: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # ------------------------------------------------------------------ #
    # 备份保留策略
    # ------------------------------------------------------------------ #
    BACKUP_RETAIN_DAILY_DAYS: int = 30
    BACKUP_RETAIN_MONTHLY_MONTHS: int = 12
    AUDIT_RETAIN_MONTHS: int = 12

    # ------------------------------------------------------------------ #
    # 限流（详见 NFR Design 第 3 节四层限流）
    # ------------------------------------------------------------------ #
    RATE_LIMIT_DEFAULT: str = "100/minute"
    RATE_LIMIT_LOGIN_IP: str = "20/minute"
    LOGIN_FAIL_TTL_SECONDS: int = 900  # 15 分钟
    LOGIN_FAIL_LIMIT_PER_IP_USERNAME: int = 5
    ACCOUNT_LOCK_THRESHOLD: int = 10

    # ------------------------------------------------------------------ #
    # 缓存 TTL
    # ------------------------------------------------------------------ #
    PERM_CACHE_TTL_SECONDS: int = 300

    # ------------------------------------------------------------------ #
    # 初始管理员
    # ------------------------------------------------------------------ #
    INITIAL_ADMIN_USERNAME: str = "admin"

    # ------------------------------------------------------------------ #
    # 导入框架（U06a）
    # ------------------------------------------------------------------ #
    IMPORT_MAX_FILE_MB: int = 20            # 上传文件大小上限（NF-6 handler 兜底层）
    IMPORT_MAX_ROWS: int = 50000            # 单文件数据行数上限（不含表头）
    IMPORT_RETENTION_DAYS: int = 0          # 0 = MVP 不清理 R2 import 文件；V1 设保留期
    IMPORT_BUCKET: str = "private"          # 导入文件 R2 桶（固定 private）

    # ------------------------------------------------------------------ #
    # 企微集成（U07）
    # ------------------------------------------------------------------ #
    WECOM_API_BASE: str = "https://qyapi.weixin.qq.com"  # 企微 API 域名（可指向 mock）
    WECOM_HTTP_TIMEOUT: int = 10            # 企微外部调用超时（秒）
    WECOM_TOKEN_TTL: int = 7000             # access_token 缓存 TTL（企微 7200 留余量）
    WECOM_URGE_SCAN_CRON: str = "0 9 * * *"  # 催发扫描调度（Asia/Shanghai）

    # ------------------------------------------------------------------ #
    # AI 决策建议（U18，DeepSeek，P3）
    # ------------------------------------------------------------------ #
    DEEPSEEK_API_BASE: str = "https://api.deepseek.com"  # DeepSeek API 域名
    DEEPSEEK_API_KEY: str = ""               # 空 → AI 端点全 503 降级
    DEEPSEEK_MODEL: str = "deepseek-chat"    # 模型名
    DEEPSEEK_TIMEOUT: int = 30               # AI 调用超时（秒）

    # ------------------------------------------------------------------ #
    # 派生属性
    # ------------------------------------------------------------------ #
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局单例 Settings。"""
    return Settings()  # type: ignore[call-arg]


# 便捷别名
settings = get_settings()
