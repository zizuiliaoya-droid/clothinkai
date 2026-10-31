"""运维开户：先预览目标，--apply 才落库；不接受命令行密码。"""

from __future__ import annotations

import argparse
import asyncio
import json

from pydantic import ValidationError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.config import settings
from app.core.db import AsyncSessionBypass, dispose_engines
from app.core.tenancy import bypass_rls_ctx
from app.modules.auth.tenant_onboarding import TenantOnboarding, provision_tenant


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="创建独立租户与管理员（默认仅预览）")
    p.add_argument("--code", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--admin-username", default="admin")
    p.add_argument(
        "--expect-environment", required=True, choices=["development", "staging", "production"]
    )
    p.add_argument("--expect-database", required=True)
    p.add_argument("--apply", action="store_true")
    return p


async def run(args: argparse.Namespace) -> int:
    try:
        request = TenantOnboarding(
            code=args.code, name=args.name, admin_username=args.admin_username
        )
    except ValidationError:
        print("开户参数无效：检查租户代码、名称和管理员用户名。")
        return 2
    target = make_url(settings.DATABASE_URL_BYPASS)
    if settings.ENVIRONMENT != args.expect_environment or target.database != args.expect_database:
        print("目标环境或数据库不匹配，未执行开户。")
        return 2
    print(
        json.dumps(
            {
                "environment": settings.ENVIRONMENT,
                "host": target.host,
                "database": target.database,
                "tenant_code": request.code,
                "tenant_name": request.name,
                "admin_username": request.admin_username,
                "mode": "apply" if args.apply else "preview",
            },
            ensure_ascii=False,
        )
    )
    if not args.apply:
        print("仅预览；核对目标后加 --apply 执行。")
        return 0
    token = bypass_rls_ctx.set(True)
    try:
        async with AsyncSessionBypass() as session, session.begin():
            result = await provision_tenant(session, request)
        # 事务成功提交后才交付；密码不进入审计，不写文件，不作为命令参数。
        print(
            json.dumps(
                {
                    "tenant_code": request.code,
                    "username": result.username,
                    "initial_password": result.initial_password,
                    "must_change_password": True,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (ValueError, IntegrityError):
        print("开户未完成：代码已占用、管理员角色缺失或数据约束冲突。事务已回滚，未覆盖账号。")
        return 1
    except SQLAlchemyError:
        print("开户数据库操作失败；未输出临时密码。请核对数据库状态后再处理。")
        return 1
    finally:
        bypass_rls_ctx.reset(token)
        await dispose_engines()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parser().parse_args())))
