"""待补款草稿的金额语义与迁移降级保护（无需数据库）。"""

from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from app.core.db import Base
from app.modules.negotiation.schemas import NegotiationCreate, NegotiationUpdate
from app.modules.negotiation.service import NegotiationService
from app.modules.promotion.enums import CooperationMode


def test_create_allows_unselected_style_and_update_tracks_explicit_clear():
    payload = NegotiationCreate(blogger_id=uuid4(), cooperation_mode="寄拍")
    assert payload.style_id is None and payload.quote_amount is None
    assert "style_id" not in NegotiationUpdate(remark="保留款式").model_fields_set
    assert "style_id" in NegotiationUpdate(style_id=None).model_fields_set


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "provided", "profile", "expected"),
    [
        (CooperationMode.CONSIGNMENT, None, None, None),
        (CooperationMode.GIFT, None, None, None),
        (CooperationMode.GIFT, Decimal("0"), Decimal("100"), Decimal("0")),
        (CooperationMode.GIFT, None, Decimal("100"), Decimal("100")),
        (CooperationMode.BARTER, None, None, Decimal("0")),
    ],
)
async def test_quote_resolution(mode, provided, profile, expected):
    svc = NegotiationService(MagicMock())
    svc._blogger_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=SimpleNamespace(quote=profile))
    )
    assert (
        await svc._resolve_quote(mode=mode, quote_amount=provided, blogger_id=uuid4()) == expected
    )


def migration():
    path = Path(__file__).parents[2] / "alembic/versions/073_style_optional_drafts.py"
    spec = spec_from_file_location("draft_migration", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = MagicMock()
    return module


def test_downgrade_refuses_incomplete_drafts_before_any_schema_change():
    module = migration()
    module.op.get_bind.return_value.execute.return_value.scalar_one.return_value = True
    with pytest.raises(RuntimeError, match="先补齐"):
        module.downgrade()
    module.op.drop_constraint.assert_not_called()
    module.op.alter_column.assert_not_called()


def test_upgrade_and_empty_downgrade_preserve_original_defaults():
    module = migration()
    module.upgrade()
    module.op.alter_column.assert_any_call("negotiation", "style_id", nullable=True)
    module.op.alter_column.assert_any_call(
        "negotiation", "quote_amount", nullable=True, server_default=None
    )
    assert module.op.create_check_constraint.call_count == 2
    module.op.reset_mock()
    module.op.get_bind.return_value.execute.return_value.scalar_one.return_value = False
    module.downgrade()
    module.op.alter_column.assert_any_call("negotiation", "style_id", nullable=False)
    assert module.op.drop_constraint.call_count == 2


def test_migration_graph_and_real_offline_upgrade_sql():
    scripts = ScriptDirectory(str(Path(__file__).parents[2] / "alembic"))
    assert len(scripts.get_heads()) == 1
    assert "073_style_optional_drafts" in {
        revision.revision for revision in scripts.walk_revisions()
    }
    assert (
        scripts.get_revision("073_style_optional_drafts").down_revision
        == "072_blogger_work_snapshots"
    )
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={
            "as_sql": True,
            "output_buffer": output,
            "target_metadata": Base.metadata,
        },
    )
    module = migration()
    module.op = op
    with Operations.context(context):
        module.upgrade()
    sql = output.getvalue()
    assert "ALTER COLUMN style_id DROP NOT NULL" in sql
    assert "ALTER COLUMN quote_amount DROP DEFAULT" in sql
    assert "ck_negotiation_ck_negotiation_submitted_complete" in sql
    assert "ck_negotiation_ck_negotiation_goods_requires_style" in sql
