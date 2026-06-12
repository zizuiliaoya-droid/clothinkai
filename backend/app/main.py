"""FastAPI 应用入口（U01）。

中间件链（外→内）：
    CORS → SentryAsgi(自动) → RequestId → slowapi Limiter → TenancyContext → Auth Dep → Router

启动序列（lifespan）：
    1. 配置 logging
    2. 初始化 Sentry（如配置）
    3. 健康检查 DB / Redis 可达
    4. ensure_initial_admin
    5. 进入服务循环
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

import sentry_sdk
import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.core.cache import close_redis
from app.core.config import settings
from app.core.db import (
    AsyncSessionBypass,
    check_db_health,
    dispose_engines,
)
from app.core.cache import check_redis_health
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware.request_id import RequestIdMiddleware
from app.core.middleware.tenancy import TenancyContextMiddleware
from app.core.tenancy import bypass_rls_ctx
from app.modules.auth.api import router as auth_router
from app.modules.blogger.api import router as blogger_router
from app.modules.product.api import router as product_router
from app.modules.product.bundle_api import router as bundle_router
from app.modules.promotion.api import router as promotion_router
from app.modules.finance.api import router as finance_router
from app.modules.finance.order_adjustment_api import router as order_adjustment_router
from app.core.attachment_api import router as attachment_router
from app.modules.importer.api import router as import_router
from app.modules.report.api import router as report_router
from app.modules.report.advanced_api import router as report_advanced_router
from app.modules.ai.api import router as ai_router
from app.modules.report.bi_api import router as bi_router
from app.modules.report.export_api import router as report_export_router
from app.modules.design.api import router as design_router
from app.modules.product.platform_product_api import router as platform_product_router
from app.modules.credential.api import router as credential_router
from app.modules.collect.crawler_api import router as crawler_router
from app.modules.collect.worker_token_api import router as worker_token_router
from app.modules.collect.data_quality_api import router as data_quality_router
from app.modules.collect.daily_data_api import router as daily_data_router
from app.modules.wecom.api import router as wecom_router
from app.modules.wecom.callback_api import router as wecom_callback_router
from app.modules.wecom.notification_api import router as notification_router
from app.modules.wecom.alert_api import router as wecom_alert_router

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limiter（slowapi）
# ---------------------------------------------------------------------------

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL_CACHE,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动 / 关闭流程。"""
    configure_logging()
    log.info("app_starting", extra={"environment": settings.ENVIRONMENT})

    # Sentry
    if settings.SENTRY_DSN_BACKEND:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN_BACKEND,
            environment=settings.ENVIRONMENT,
            integrations=[
                FastApiIntegration(),
                SqlalchemyIntegration(),
            ],
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,
            before_send=_strip_sensitive_for_sentry,
        )
        log.info("sentry_initialized")

    # 健康检查（启动失败应让容器拉起者发现）
    db_ok = await check_db_health()
    if not db_ok:
        log.error("startup_db_unreachable")

    redis_ok = await check_redis_health()
    if not redis_ok:
        log.error("startup_redis_unreachable")

    # 初始化管理员
    try:
        await _ensure_initial_admin()
    except Exception as exc:  # noqa: BLE001
        log.exception("ensure_initial_admin_failed")
        sentry_sdk.capture_exception(exc)

    # 注册跨单元事件监听器（U04 引入；U05 监听 SettlementRequested）
    register_event_listeners()

    # 注册导入 Adapter（U06a；worker 进程由 celery worker_process_init 单独注册）
    register_import_adapters()

    yield

    # 关闭
    log.info("app_shutting_down")
    await dispose_engines()
    await close_redis()


# ---------------------------------------------------------------------------
# 事件监听器注册（U04 / FB3）
# ---------------------------------------------------------------------------


def register_event_listeners() -> None:
    """注册所有跨单元事件监听器。

    策略（FB3 修正 + FB6 防重复注册）：
    - ``clear_handlers()`` 启动前清空，防热重载累计
    - **仅捕获 ModuleNotFoundError**（U05 finance 模块尚未部署的预期场景）
      → warning + Sentry breadcrumb
    - 其他 ImportError / Exception → fail fast，refuse to start

    注意：当 U05 finance 未部署而 U04 已部署时，``SettlementRequested`` 事件
    在 dispatch 时会抛 ``MissingRequiredHandlerError``，导致 review approve 失败
    （强一致：失败比"待付款 + 无 settlement 记录"更安全）。
    """
    from app.core.events import clear_handlers

    clear_handlers()

    try:
        from app.modules.finance.listeners import register as register_finance  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        log.warning(
            "u05_finance_module_not_found_skipping_listener_registration. "
            "SettlementRequested events will fail with MissingRequiredHandlerError "
            "until U05 is deployed."
        )
        sentry_sdk.add_breadcrumb(
            message="U05 finance module not found",
            level="warning",
        )
        return

    try:
        register_finance()
    except Exception as exc:
        log.exception(
            "listener_registration_failed", extra={"module": "finance"}
        )
        raise RuntimeError(
            "U05 finance listener registration failed, refusing to start"
        ) from exc

    # 第 2 步：U04 promotion 反向 listener（通知类 SettlementPaid，FB5）
    # 缺失不阻塞（required_handler=False）；存在但注册失败 fail fast
    try:
        from app.modules.promotion.listeners import (  # type: ignore[import-not-found]
            register as register_promotion_listeners,
        )
    except ModuleNotFoundError:
        log.warning(
            "promotion_listeners_module_not_found_skipping. "
            "SettlementPaid events will be dropped (acceptable; required_handler=False)."
        )
        return

    try:
        register_promotion_listeners()
    except Exception as exc:
        log.exception(
            "listener_registration_failed", extra={"module": "promotion"}
        )
        raise RuntimeError(
            "U04 promotion listener registration failed, refusing to start"
        ) from exc

    # 第 3 步：U15 wecom 反向 listener（通知类 PromotionPublished，S09 控评通知）
    # 缺失不阻塞（required_handler=False）；存在但注册失败 fail fast
    try:
        from app.modules.wecom.listeners import (  # type: ignore[import-not-found]
            register as register_wecom_listeners,
        )
    except ModuleNotFoundError:
        log.warning(
            "wecom_listeners_module_not_found_skipping. "
            "PromotionPublished events will be dropped (acceptable; required_handler=False)."
        )
        return

    try:
        register_wecom_listeners()
    except Exception as exc:
        log.exception(
            "listener_registration_failed", extra={"module": "wecom"}
        )
        raise RuntimeError(
            "U15 wecom listener registration failed, refusing to start"
        ) from exc


# ---------------------------------------------------------------------------
# 导入 Adapter 注册（U06a / NF-4）
# ---------------------------------------------------------------------------


def register_import_adapters() -> None:
    """注册所有导入 Adapter（U06a 框架，具体 Adapter 由 U06b/c/d/e 提供）。

    双进程加载（NF-4）：
    - HTTP 进程：lifespan 调用本函数（upload 端点 source 白名单校验需要）
    - worker 进程：``app.tasks.import_tasks`` 的 ``worker_process_init`` 信号调用本函数
      （HTTP 进程的注册 worker 子进程看不到）

    各 Adapter 模块未部署（U06b~e 尚未交付）时仅 warning 不阻塞 —— 框架先行可独立部署。
    """
    import importlib

    from app.modules.importer.registry import ImportAdapterRegistry

    adapter_modules = [
        "app.modules.importer.adapters.style_sku",   # U06b
        "app.modules.importer.adapters.blogger",      # U06c
        "app.modules.importer.adapters.promotion",    # U06d
        "app.modules.importer.adapters.settlement",   # U06e
        "app.modules.importer.adapters.qianniu",       # U13
        "app.modules.importer.adapters.wanxiangtai",   # U13
        "app.modules.importer.adapters.huitun",        # U13
    ]
    for mod_name in adapter_modules:
        try:
            module = importlib.import_module(mod_name)
            register_fn = getattr(module, "register", None)
            if register_fn is not None:
                register_fn()
        except ModuleNotFoundError:
            log.warning(
                "import_adapter_module_not_found",
                extra={"module": mod_name},
            )
    log.info(
        "import_adapters_registered",
        extra={"sources": sorted(ImportAdapterRegistry.sources())},
    )


# ---------------------------------------------------------------------------
# Sentry before_send
# ---------------------------------------------------------------------------


def _strip_sensitive_for_sentry(event: dict, _hint: dict) -> dict:
    """过滤可能泄露的敏感字段。"""
    sensitive = (
        "password",
        "old_password",
        "new_password",
        "token",
        "secret",
        "access_token",
        "refresh_token",
        "authorization",
    )
    request = event.get("request") or {}
    data = request.get("data")
    if isinstance(data, dict):
        for key in list(data.keys()):
            if key.lower() in sensitive:
                data[key] = "[Filtered]"
    headers = request.get("headers")
    if isinstance(headers, dict):
        for key in list(headers.keys()):
            if key.lower() in sensitive:
                headers[key] = "[Filtered]"
    return event


# ---------------------------------------------------------------------------
# ensure_initial_admin
# ---------------------------------------------------------------------------


async def _ensure_initial_admin() -> None:
    """首次启动时创建初始管理员（BR-INIT-002）。"""
    from sqlalchemy import select

    from app.core.security.auth import hash_password
    from app.modules.auth.domain import generate_random_password
    from app.modules.auth.models import Role, Tenant, User, UserRole
    from app.modules.auth.repository import AuditLogRepository  # noqa: F401

    token = bypass_rls_ctx.set(True)
    try:
        async with AsyncSessionBypass() as session:
            # 找 default tenant
            tenant = (
                await session.execute(select(Tenant).where(Tenant.code == "default"))
            ).scalar_one_or_none()
            if tenant is None:
                log.warning("no_default_tenant_yet_skip_admin_init")
                return

            # 找 admin role
            admin_role = (
                await session.execute(select(Role).where(Role.code == "admin"))
            ).scalar_one_or_none()
            if admin_role is None:
                log.warning("no_admin_role_yet_skip_admin_init")
                return

            # 找是否已有管理员
            existing_admin = (
                await session.execute(
                    select(User)
                    .join(UserRole, UserRole.user_id == User.id)
                    .where(
                        User.tenant_id == tenant.id,
                        User.deleted_at.is_(None),
                        UserRole.role_id == admin_role.id,
                    )
                )
            ).scalars().first()
            if existing_admin is not None:
                log.info("initial_admin_exists")
                return

            # 创建
            plain = generate_random_password(16)
            user = User(
                tenant_id=tenant.id,
                username=settings.INITIAL_ADMIN_USERNAME,
                password_hash=hash_password(plain),
                display_name="Initial Admin",
                status="active",
                password_must_change=True,
                password_changed_at=datetime.now(timezone.utc),
            )
            session.add(user)
            await session.flush()
            session.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=admin_role.id))
            await session.commit()

            # 仅一次性 stdout 输出（不写日志）
            print("\n" + "=" * 60)
            print("[U01] Initial admin created.")
            print(f"  Username: {settings.INITIAL_ADMIN_USERNAME}")
            print(f"  Password: {plain}")
            print("  ⚠️  IMMEDIATELY save this password and change it on first login.")
            print("=" * 60 + "\n")
    finally:
        bypass_rls_ctx.reset(token)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(
        title="Clothing ERP API",
        description="服装电商运营管理系统后端 API",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # ----- Limiter（slowapi）-----
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ----- 中间件（注册顺序与执行顺序相反）-----
    # 内层（最后注册 → 最先执行）
    app.add_middleware(TenancyContextMiddleware)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(RequestIdMiddleware)
    # 外层
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # ----- 错误处理 -----
    register_error_handlers(app)

    # ----- Prometheus -----
    Instrumentator(
        excluded_handlers=["/health", "/ready", "/metrics"],
        should_group_status_codes=False,
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    # ----- 健康检查 -----
    @app.get("/health", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    async def readiness() -> JSONResponse:
        checks = {"db": "ok", "redis": "ok"}
        if not await check_db_health():
            checks["db"] = "error"
        if not await check_redis_health():
            checks["redis"] = "error"
        overall = "ok" if all(v == "ok" for v in checks.values()) else "error"
        status_code = (
            status.HTTP_200_OK if overall == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return JSONResponse(
            content={"status": overall, "checks": checks},
            status_code=status_code,
        )

    # ----- 路由挂载 -----
    app.include_router(auth_router, prefix="/api")
    app.include_router(product_router)  # product router 已含 /api 前缀
    app.include_router(bundle_router)  # U17 套装 /api/bundles
    app.include_router(blogger_router)  # blogger router 已含 /api 前缀
    app.include_router(promotion_router)  # promotion router 已含 /api 前缀
    app.include_router(finance_router)  # finance router 已含 /api 前缀
    app.include_router(order_adjustment_router)  # U16 拍单/刷单/余额 /api/finance
    app.include_router(attachment_router)  # shared attachment router 已含 /api 前缀
    app.include_router(import_router)  # U06a importer router 已含 /api/imports 前缀
    app.include_router(wecom_router)  # U07 wecom router 已含 /api 前缀
    app.include_router(wecom_callback_router)  # U07 公开回调（无 JWT，签名校验）
    app.include_router(notification_router)  # U07 站内通知 /api/notifications
    app.include_router(wecom_alert_router)  # U15 企微预警配置 /api/wecom/alert-config
    app.include_router(report_router)  # U08 发文进度看板 /api/reports/publish-progress
    app.include_router(report_advanced_router)  # U14 报表进阶 /api/reports/{work-progress,targets,store-daily,production}
    app.include_router(bi_router)  # U17 BI 看板 /api/reports/bi
    app.include_router(report_export_router)  # U17 报表导出 /api/reports/{type}/export
    app.include_router(ai_router)  # U18 AI 决策建议 /api/ai
    app.include_router(design_router)  # U10a 设计制版 /api/designs
    app.include_router(platform_product_router)  # U10b 平台商品映射 /api/platform-products
    app.include_router(credential_router)  # U12 平台凭据 /api/credentials
    app.include_router(crawler_router)  # U13 采集 Worker /api/crawler/tasks
    app.include_router(worker_token_router)  # U13 Worker Token /api/crawler/worker-tokens
    app.include_router(data_quality_router)  # U13 数据质量看板 /api/data-quality
    app.include_router(daily_data_router)  # 千牛/站内日报列表 /api/qianniu /api/ad-daily

    return app


app = create_app()
