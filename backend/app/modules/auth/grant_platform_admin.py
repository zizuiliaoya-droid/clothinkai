"""明确运维批准后的平台身份初始化；默认预览，不重设密码/原有角色。"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.config import settings
from app.core.db import AsyncSessionBypass, dispose_engines
from app.core.security.permissions import invalidate_user_permissions_cache
from app.core.tenancy import bypass_rls_ctx
from app.modules.auth.models import Role, Tenant, User, UserRole
from app.modules.auth.platform_access import PLATFORM_ADMIN, has_platform_role
from app.modules.auth.repository import RefreshTokenRepository


async def grant(session: AsyncSession, username: str) -> tuple[User, bool]:
    if not bypass_rls_ctx.get():
        raise ValueError("需要运维会话")
    tenant = (
        await session.execute(
            select(Tenant).where(
                Tenant.code == "default", Tenant.status == "active", Tenant.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if tenant is None:
        raise ValueError("default 租户不可用")
    user = (
        await session.execute(
            select(User)
            .where(
                User.tenant_id == tenant.id,
                User.username == username,
                User.status == "active",
                User.deleted_at.is_(None),
                User.locked_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if user is None:
        raise ValueError("目标账号不存在、已停用或锁定")
    if await has_platform_role(session, user.id):
        return user, False
    role = (
        await session.execute(select(Role).where(Role.code == PLATFORM_ADMIN))
    ).scalar_one_or_none()
    if role is None:
        raise ValueError("缺少系统预设 platform_admin 角色")
    session.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=role.id))
    user.password_changed_at = datetime.now(UTC)
    await RefreshTokenRepository(session).revoke_all_for_user(user.id)
    await AuditService(session).log(
        action="platform_role_grant",
        actor_type="system",
        tenant_id=tenant.id,
        resource="user",
        resource_id=user.id,
        after={"added_role": PLATFORM_ADMIN, "username": username},
    )
    await session.flush()
    return user, True


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="给 default 的明确账号追加平台身份；默认仅预览")
    result.add_argument("--username", required=True)
    result.add_argument(
        "--expect-environment", required=True, choices=["development", "staging", "production"]
    )
    result.add_argument("--expect-database", required=True)
    result.add_argument("--apply", action="store_true")
    return result


async def run(args: argparse.Namespace) -> int:
    target = make_url(settings.DATABASE_URL_BYPASS)
    if settings.ENVIRONMENT != args.expect_environment or target.database != args.expect_database:
        print("目标环境或数据库不匹配，未授权平台身份。")
        return 2
    print(
        json.dumps(
            {
                "environment": settings.ENVIRONMENT,
                "host": target.host,
                "database": target.database,
                "tenant_code": "default",
                "username": args.username,
                "mode": "apply" if args.apply else "preview",
            },
            ensure_ascii=False,
        )
    )
    if not args.apply:
        print("仅预览。确认账号和本次授权后加 --apply；保留原密码与业务角色。")
        return 0
    token = bypass_rls_ctx.set(True)
    try:
        async with AsyncSessionBypass() as session, session.begin():
            user, changed = await grant(session, args.username)
            if changed:
                await invalidate_user_permissions_cache(str(user.id))
        # 与普通角色调整一致：缓存清理成功后提交身份和安全戳。
        print(
            json.dumps(
                {
                    "tenant_code": "default",
                    "username": args.username,
                    "platform_admin": True,
                    "added": changed,
                    "login_again": changed,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (ValueError, SQLAlchemyError, RedisError):
        print("初始化未完成，请核对目标账号与数据库状态；未输出凭据。")
        return 1
    finally:
        bypass_rls_ctx.reset(token)
        await dispose_engines()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parser().parse_args())))
