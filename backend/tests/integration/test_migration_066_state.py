"""迁移 066_platform_metric_setting 在 head 状态下的库结构与 seed（报表视图设计 3.4、第 4 节）。

往返（064 ↔ 066）与生产形态演练（1 个租户、有覆盖行、带一条 metric_config 个人授权时下行）
在测试库上手工跑，结果写报告，不进仓库。scope 角色集合与 ``DEFAULT_ROLES`` 的一致性、表的 RLS
在 test_flow_rls_schema.py。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.default_roles import DEFAULT_ROLES, METRIC_CONFIG_READ, METRIC_CONFIG_WRITE
from app.modules.metric_config.models import PromotionPlatformSetting

_TABLE = "promotion_platform_setting"
_APP_ROLE = "clothing_app"
# 设计 2.7 / 2.8 的默认值（系数 = 旧 PLATFORM_LIKE_COEFFICIENT，阈值 = 抖音 10000、其余 1000）
_DEFAULTS = {
    "小红书": (Decimal("1.000"), 1000),
    "抖音": (Decimal("0.100"), 10000),
    "快手": (Decimal("0.100"), 1000),
    "B站": (Decimal("1.000"), 1000),
    "得物": (Decimal("1.000"), 1000),
}
_CK_LIKE = "ck_promotion_platform_setting_like_coefficient"
_CK_HIT = "ck_promotion_platform_setting_hit_threshold"
_UQ = "uq_promotion_platform_setting_tenant_platform"


async def _constraints(session: AsyncSession, contype: str) -> dict[str, str]:
    return dict(
        (
            await session.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = CAST(:t AS regclass) AND CAST(contype AS text) = CAST(:c AS text)"
                ),
                {"t": _TABLE, "c": contype},
            )
        )
        .tuples()
        .all()
    )


async def _roles_for(session: AsyncSession, scope: str) -> set[str]:
    return set(
        (
            await session.execute(
                text(
                    "SELECT r.code FROM role_permission rp "
                    "JOIN role r ON r.id = rp.role_id "
                    "JOIN permission p ON p.id = rp.permission_id "
                    "WHERE p.scope = CAST(:s AS text)"
                ),
                {"s": scope},
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration066Table:
    async def test_columns(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, character_maximum_length, "
                    "numeric_precision, numeric_scale FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = CAST(:t AS text)"
                ),
                {"t": _TABLE},
            )
        ).all()
        got = {r.column_name: tuple(r[1:]) for r in rows}
        assert got == {
            "id": ("uuid", "NO", None, None, None),
            "tenant_id": ("uuid", "NO", None, None, None),
            "created_at": ("timestamp with time zone", "NO", None, None, None),
            "updated_at": ("timestamp with time zone", "NO", None, None, None),
            "platform": ("character varying", "NO", 16, None, None),
            "like_coefficient": ("numeric", "NO", None, 6, 3),
            "hit_threshold": ("integer", "NO", None, 32, 0),
            "updated_by": ("uuid", "YES", None, None, None),
        }

    async def test_checks_real_names(self, session: AsyncSession) -> None:
        checks = await _constraints(session, "c")
        assert set(checks) == {_CK_LIKE, _CK_HIT}
        assert checks[_CK_LIKE] == (
            "CHECK (((like_coefficient > (0)::numeric) AND (like_coefficient <= (10)::numeric)))"
        )
        assert checks[_CK_HIT] == "CHECK (((hit_threshold >= 1) AND (hit_threshold <= 10000000)))"

    async def test_foreign_keys(self, session: AsyncSession) -> None:
        fks = set((await _constraints(session, "f")).values())
        assert fks == {
            "FOREIGN KEY (tenant_id) REFERENCES tenant(id) ON DELETE RESTRICT",
            'FOREIGN KEY (updated_by) REFERENCES "user"(id) ON DELETE SET NULL',
        }

    async def test_unique_index(self, session: AsyncSession) -> None:
        idx = dict(
            (
                await session.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE tablename = CAST(:t AS text)"
                    ),
                    {"t": _TABLE},
                )
            )
            .tuples()
            .all()
        )
        assert idx[_UQ].startswith("CREATE UNIQUE INDEX")
        assert idx[_UQ].endswith("(tenant_id, platform)")

    async def test_app_role_privileges(self, session: AsyncSession) -> None:
        got = {
            p: (
                await session.execute(
                    text(
                        "SELECT has_table_privilege(CAST(:r AS text), CAST(:t AS text), "
                        "CAST(:p AS text))"
                    ),
                    {"r": _APP_ROLE, "t": f"public.{_TABLE}", "p": p},
                )
            ).scalar_one()
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }
        assert got == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": True}

    async def test_default_tenant_seeded(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT s.platform, s.like_coefficient, s.hit_threshold, s.updated_by "
                    "FROM promotion_platform_setting s JOIN tenant t ON t.id = s.tenant_id "
                    "WHERE t.code = 'default'"
                )
            )
        ).all()
        assert {r.platform: (r.like_coefficient, r.hit_threshold) for r in rows} == _DEFAULTS
        assert len(rows) == 5
        assert {r.updated_by for r in rows} == {None}


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration066Seed:
    @pytest.mark.parametrize(
        ("scope", "roles"),
        [
            (METRIC_CONFIG_READ, {"pr_manager", "operations"}),
            (METRIC_CONFIG_WRITE, {"pr_manager"}),
        ],
    )
    async def test_scope_roles(self, session: AsyncSession, scope: str, roles: set[str]) -> None:
        categories = (
            (
                await session.execute(
                    text("SELECT category FROM permission WHERE scope = CAST(:s AS text)"),
                    {"s": scope},
                )
            )
            .scalars()
            .all()
        )
        assert categories == ["function"]
        in_db = await _roles_for(session, scope)
        assert in_db == roles
        assert in_db == {r.code for r in DEFAULT_ROLES if scope in r.permissions}


class TestOrmMatchesMigration066:
    def test_columns(self) -> None:
        cols = PromotionPlatformSetting.__table__.columns
        assert {c.name for c in cols} == {
            "id",
            "tenant_id",
            "created_at",
            "updated_at",
            "platform",
            "like_coefficient",
            "hit_threshold",
            "updated_by",
        }
        assert (cols["platform"].type.length, cols["platform"].nullable) == (16, False)  # type: ignore[attr-defined]
        coef = cols["like_coefficient"].type
        assert (coef.precision, coef.scale, cols["like_coefficient"].nullable) == (6, 3, False)  # type: ignore[attr-defined]
        assert cols["hit_threshold"].nullable is False
        assert cols["updated_by"].nullable is True
        fk = next(iter(cols["updated_by"].foreign_keys))
        assert (fk.column.table.name, fk.ondelete) == ("user", "SET NULL")

    def test_checks_and_unique(self) -> None:
        table = PromotionPlatformSetting.__table__
        by_name = {str(c.name): c for c in table.constraints}
        assert (
            str(by_name[_CK_LIKE].sqltext)  # type: ignore[attr-defined]
            == "like_coefficient > 0 AND like_coefficient <= 10"
        )
        assert (
            str(by_name[_CK_HIT].sqltext)  # type: ignore[attr-defined]
            == "hit_threshold >= 1 AND hit_threshold <= 10000000"
        )
        uq = {i.name: i for i in table.indexes}[_UQ]
        assert uq.unique is True
        assert [c.name for c in uq.columns] == ["tenant_id", "platform"]


_PROBE = textwrap.dedent(
    """
    import app.modules.metric_config.models  # noqa: F401
    from sqlalchemy import insert
    from sqlalchemy.orm import configure_mappers
    from sqlalchemy.sql.ddl import sort_tables_and_constraints

    from app.core.db import Base
    from app.modules.metric_config.models import PromotionPlatformSetting

    configure_mappers()
    t = Base.metadata.tables["promotion_platform_setting"]
    missing = [
        fk.target_fullname
        for fk in t.foreign_keys
        if fk.target_fullname.split(".")[0] not in Base.metadata.tables
    ]
    if missing:
        raise SystemExit("MISSING_FK_TARGETS: " + "; ".join(missing))
    sort_tables_and_constraints([t])
    str(insert(PromotionPlatformSetting).compile())
    print("PERSIST_READY")
    """
)


class TestModelImportStandalone:
    def test_models_alone(self) -> None:
        """干净子进程只 import 本模型：FK 目标（tenant、user）要都在 metadata 里（Celery 路径同理）。"""
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=180
        )
        assert "PERSIST_READY" in proc.stdout, f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"

    def test_celery_task_chain(self) -> None:
        """干净子进程只 import 博主任务模块（生产 worker 走的路径，S5 起会读本表）。"""
        code = (
            "import sys\n"
            "from app.tasks import blogger_tasks  # noqa: F401\n"
            # 任务链本身要走到本模型（重算读配置），不靠下面显式 import
            "assert 'app.modules.metric_config.models' in sys.modules, 'CHAIN_MISSING_MODEL'\n"
        ) + _PROBE.replace("import app.modules.metric_config.models  # noqa: F401\n", "")
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=180
        )
        assert "PERSIST_READY" in proc.stdout, f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
