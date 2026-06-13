"""补齐 clothing_app/clothing_bypass 表与序列权限（迁移以 clothing_bypass 建表，
默认权限不覆盖业务角色，导致 clothing_app 无 CRUD 权限 → /me 等 500）。

本迁移幂等地：
- GRANT 全部表/序列 CRUD 给 clothing_app + clothing_bypass
- 重申 audit_log append-only（REVOKE UPDATE/DELETE FROM clothing_app；archiver 可 DELETE）
- 设 ALTER DEFAULT PRIVILEGES FOR ROLE clothing_bypass，使后续迁移新建表自动授权 clothing_app

Revision ID: 025_grant_app_priv
Revises: 024_promo_source
Create Date: 2026-06-13
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "025_grant_app_priv"
down_revision: str | Sequence[str] | None = "024_promo_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) 业务角色拿到现存全部表/序列的 CRUD（迁移由 clothing_bypass 建表，作为 owner 可授权）
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        "TO clothing_app, clothing_bypass"
    )
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
        "TO clothing_app, clothing_bypass"
    )

    # 2) 重申 audit_log append-only（上面的广义 GRANT 会把 UPDATE/DELETE 加回来）
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM clothing_app")
    op.execute("GRANT SELECT, DELETE ON audit_log TO clothing_archiver")
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO clothing_archiver"
    )

    # 3) 后续迁移（由 clothing_bypass 执行）新建表/序列自动授权 clothing_app
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE clothing_bypass IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO clothing_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE clothing_bypass IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO clothing_app"
    )


def downgrade() -> None:
    # 撤销默认权限规则（不回收已授予的具体权限，避免破坏运行中实例）
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE clothing_bypass IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM clothing_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE clothing_bypass IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM clothing_app"
    )
