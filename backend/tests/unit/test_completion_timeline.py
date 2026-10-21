"""M5存量完结只还原迁移标记，真实事件不重复；仍按推广分组过滤。"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.security.field_permissions import FieldPermissionContext
from app.modules.flow.timeline import legacy_points
from app.modules.flow.timeline_repository import LineHeader
from app.modules.promotion.models import Promotion

pytestmark = pytest.mark.unit
AT = datetime(2026, 10, 21, tzinfo=UTC)


def test_migration_completion_marker_survives_first_event_cutoff() -> None:
    promotion = Promotion(completed_at=AT)
    header = LineHeader(promotion, None, False, None)
    points = legacy_points(
        header,
        first_event_at=AT - timedelta(days=1),
        ctx=FieldPermissionContext(
            role_codes=frozenset({"pr_manager"}), grants=frozenset(), revokes=frozenset()
        ),
        sees_negotiation=False,
    )
    assert [(p.text, p.source, p.at) for p in points] == [
        ("上线时完结", "promotion.completed_at", AT)
    ]


def test_real_completion_event_and_revoked_group_hide_migration_marker() -> None:
    promotion = Promotion(completed_at=AT)
    for has_event, revokes in [
        (True, frozenset()),
        (False, frozenset({"field.line_event.promotion:read"})),
    ]:
        header = LineHeader(promotion, None, False, None)
        points = legacy_points(
            header,
            first_event_at=None,
            has_completion_event=has_event,
            ctx=FieldPermissionContext(
                role_codes=frozenset({"pr_manager"}), grants=frozenset(), revokes=revokes
            ),
            sees_negotiation=False,
        )
        assert not any(p.source == "promotion.completed_at" for p in points)
