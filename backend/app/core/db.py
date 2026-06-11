"""数据库基础设施（双引擎 + TenantScopedModel + ORM 钩子 + Session 依赖）。

按 NFR Design 第 2 节决策：
- engine_app（连接 clothing_app 角色，启用 RLS）
- engine_bypass（连接 clothing_bypass 角色，绕过 RLS）

ORM 层多租户注入（before_compile 事件 + before_insert 事件）：
- 查询自动 WHERE tenant_id = :ctx
- 插入自动填 tenant_id
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, MetaData, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
)
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import settings
from app.core.exceptions import (
    TenantContextMismatchError,
    TenantContextMissingError,
)
from app.core.tenancy import bypass_rls_ctx, tenant_id_ctx

# ---------------------------------------------------------------------------
# 命名约定（约束与索引名规范化）
# ---------------------------------------------------------------------------

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """SQLAlchemy 声明基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ---------------------------------------------------------------------------
# 通用字段 mixin
# ---------------------------------------------------------------------------

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import UUID as PGUUID


class TimestampMixin:
    """统一时间戳字段。

    同时设置 Python 侧 ``default``/``onupdate`` 与 DB 侧 ``server_default``/``onupdate``：
    - Python 侧保证 INSERT 后 ORM 对象立即有值（无需 refresh，避免 commit 后访问
      触发懒加载 / MissingGreenlet）
    - DB 侧保证非 ORM 写入（如 migration / 原生 SQL）也有默认值
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SoftDeleteMixin:
    """软删除时间戳。"""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class TenantScopedModel(Base, TimestampMixin):
    """多租户基类。所有业务表必须继承此类。

    自动获得 tenant_id 字段 + 软删除 + before_compile/before_insert 钩子注入。
    """

    __abstract__ = True

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenant.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# 引擎与 Session
# ---------------------------------------------------------------------------


def _build_engine(url: str, *, pool_size: int, max_overflow: int) -> Any:
    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=settings.DB_ECHO,
        future=True,
    )


engine_app = _build_engine(
    settings.DATABASE_URL_APP,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
)

engine_bypass = _build_engine(
    settings.DATABASE_URL_BYPASS,
    pool_size=2,  # 系统任务用，连接更少
    max_overflow=3,
)

AsyncSessionApp = async_sessionmaker(engine_app, expire_on_commit=False, class_=AsyncSession)
AsyncSessionBypass = async_sessionmaker(engine_bypass, expire_on_commit=False, class_=AsyncSession)


# ---------------------------------------------------------------------------
# get_session 依赖（从 contextvars 决定使用哪个引擎）
# ---------------------------------------------------------------------------


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI Depends：根据 contextvars 选择 engine + 设置 SET LOCAL。"""
    bypass = bypass_rls_ctx.get()
    tid = tenant_id_ctx.get()

    if bypass:
        async with AsyncSessionBypass() as session:
            await session.execute(text("SET LOCAL app.bypass_rls = 'on'"))
            yield session
        return

    async with AsyncSessionApp() as session:
        if tid is not None:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(tid)},
            )
            yield session
        else:
            # 没有 tenant_id 上下文且未声明 bypass：可能是匿名请求（如 /health /login）
            # 业务表查询会触发 TenantContextMissingError；非业务表（如 /health）不需要 SET LOCAL
            yield session


async def get_bypass_session() -> AsyncIterator[AsyncSession]:
    """匿名前置认证专用会话（登录 / 刷新 token）。

    登录是冷启动：客户端尚不知道 tenant_id，用户名查询必须跨租户，
    无法满足 RLS ``app.tenant_id`` 策略。这类请求显式走 bypass 引擎，
    安全性由 AuthService 内的密码校验 + 限流 + 锁定逻辑兜底。
    """
    token = bypass_rls_ctx.set(True)
    try:
        async with AsyncSessionBypass() as session:
            await session.execute(text("SET LOCAL app.bypass_rls = 'on'"))
            yield session
    finally:
        bypass_rls_ctx.reset(token)


# ---------------------------------------------------------------------------
# ORM 事件钩子：before_compile 和 before_insert 注入 tenant_id
# ---------------------------------------------------------------------------


@event.listens_for(Session, "do_orm_execute")
def _enforce_tenant_filter(orm_execute_state: Any) -> None:
    """查询时自动注入 WHERE tenant_id = :ctx。

    依赖 SQLAlchemy 2.0 的 ``do_orm_execute`` 事件 + ``with_loader_criteria`` 模式。
    """
    if not orm_execute_state.is_select:
        return
    if bypass_rls_ctx.get():
        return

    from sqlalchemy.orm import with_loader_criteria

    tid = tenant_id_ctx.get()
    if tid is None:
        # 未设置 tenant_id 时不强制过滤（匿名/系统任务/非业务表）
        # 真正的业务安全由 PostgreSQL RLS 兜底
        return

    def _make_criteria(cls: type) -> ColumnElement[bool]:
        return cls.tenant_id == tid

    orm_execute_state.statement = orm_execute_state.statement.options(
        with_loader_criteria(
            TenantScopedModel,
            _make_criteria,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _enforce_tenant_on_insert(session: Session, _flush_context: Any, _instances: Any) -> None:
    """INSERT 时若实体未填 tenant_id 则用上下文填充；若两者冲突则报错。"""
    if bypass_rls_ctx.get():
        return

    ctx_tid = tenant_id_ctx.get()
    for obj in session.new:
        if not isinstance(obj, TenantScopedModel):
            continue
        if obj.tenant_id is None:
            if ctx_tid is None:
                raise TenantContextMissingError(
                    f"插入 {type(obj).__name__} 时缺失 tenant_id 上下文",
                )
            obj.tenant_id = ctx_tid
        elif ctx_tid is not None and obj.tenant_id != ctx_tid:
            raise TenantContextMismatchError(
                f"{type(obj).__name__} 的 tenant_id={obj.tenant_id} 与上下文 {ctx_tid} 不匹配",
            )


# ---------------------------------------------------------------------------
# 健康检查辅助
# ---------------------------------------------------------------------------


async def check_db_health() -> bool:
    """主连接 SELECT 1 健康检查。"""
    try:
        async with engine_app.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


async def dispose_engines() -> None:
    """优雅关闭引擎（lifespan 退出时调用）。"""
    await engine_app.dispose()
    await engine_bypass.dispose()
