"""请求摘要、逐项隔离和动作重复项；不连接数据库。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import PermissionDeniedError
from app.modules.negotiation.bulk_schemas import (
    BulkActionRequest,
    BulkCreateRequest,
    BulkItemResult,
    BulkLookupRequest,
)
from app.modules.negotiation.bulk_service import BulkNegotiationService, creation_request_hash
from app.modules.negotiation.exceptions import NegotiationStateConflictError
from app.modules.negotiation.schemas import NegotiationCreate


def test_hash_is_canonical_without_live_prices():
    a, b, group = uuid4(), uuid4(), uuid4()
    left = NegotiationCreate(
        blogger_id=a, account_ids=[a, b], cooperation_mode="送拍", quote_amount="0"
    )
    right = NegotiationCreate(
        blogger_id=a, account_ids=[b, a, b], cooperation_mode="送拍", quote_amount="0.00"
    )
    assert creation_request_hash(left, group) == creation_request_hash(right, group)
    assert creation_request_hash(left, group) != creation_request_hash(left, uuid4())
    omitted = NegotiationCreate(blogger_id=a, cooperation_mode="送拍")
    defaulted = NegotiationCreate(
        blogger_id=a,
        account_ids=[a],
        cooperation_mode="送拍",
        platform="小红书",
        items=None,
        remark=None,
    )
    assert creation_request_hash(omitted, group) == creation_request_hash(defaulted, group)
    explicit = NegotiationCreate(blogger_id=a, cooperation_mode="送拍", quote_amount=None)
    assert creation_request_hash(omitted, group) != creation_request_hash(explicit, group)
    assert creation_request_hash(omitted, group) != creation_request_hash(left, group)


@pytest.mark.parametrize("count", [0, 51])
def test_bulk_max_fifty_and_nonempty(count):
    with pytest.raises(ValidationError):
        BulkCreateRequest(
            creation_group_id=uuid4(),
            items=[{"client_create_key": uuid4(), "payload": {}} for _ in range(count)],
        )
    with pytest.raises(ValidationError):
        BulkActionRequest(items=[{"negotiation_id": uuid4()} for _ in range(count)])
    with pytest.raises(ValidationError):
        BulkLookupRequest(client_create_keys=[uuid4() for _ in range(count)])


def test_creation_metadata_cannot_be_written_by_single_input():
    payload = NegotiationCreate.model_validate(
        {
            "blogger_id": str(uuid4()),
            "cooperation_mode": "送拍",
            "client_create_key": str(uuid4()),
            "creation_pr_id": str(uuid4()),
            "creation_request_hash": "forged",
        }
    )
    assert (
        not {"client_create_key", "creation_pr_id", "creation_request_hash"}
        & payload.model_dump().keys()
    )


@pytest.mark.asyncio
async def test_mixed_results_rollback_failed_item_and_skip_duplicate(monkeypatch):
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = BulkNegotiationService(session)
    actor = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    service._user = AsyncMock(return_value=actor)
    ids = [uuid4() for _ in range(4)]

    async def act(user, *, action, item):
        if item.negotiation_id == ids[1]:
            raise PermissionDeniedError()
        if item.negotiation_id == ids[2]:
            raise NegotiationStateConflictError("其他人已处理")
        if item.negotiation_id == ids[3]:
            raise RuntimeError("internal secret should not appear")
        return BulkItemResult(
            row_key=str(item.negotiation_id),
            negotiation_id=item.negotiation_id,
            status="success",
            code="OK",
            message="ok",
        )

    action = AsyncMock(side_effect=act)
    monkeypatch.setattr(service, "_act", action)
    result = await service.perform(
        "submit",
        BulkActionRequest(
            items=[{"negotiation_id": value} for value in [ids[0], ids[1], ids[2], ids[3], ids[0]]]
        ),
        actor,
    )
    assert (result.success, result.skipped, result.failed) == (1, 3, 1)
    assert [r.code for r in result.results] == [
        "OK",
        "PERMISSION_DENIED",
        "NEGOTIATION_STATE_CONFLICT",
        "INTERNAL_ERROR",
        "DUPLICATE_ITEM",
    ]
    assert "secret" not in result.model_dump_json()
    assert action.await_count == 4
    assert session.commit.await_count == 1 and session.rollback.await_count == 3


@pytest.mark.asyncio
async def test_bad_create_payload_is_one_failed_row(monkeypatch):
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = BulkNegotiationService(session)
    actor = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    service._user = AsyncMock(return_value=actor)
    existing = AsyncMock(return_value=None)
    monkeypatch.setattr(service, "_existing", existing)
    result = await service.create(
        BulkCreateRequest(
            creation_group_id=uuid4(),
            items=[
                {"client_create_key": uuid4(), "payload": {"blogger_id": "invalid-private-input"}}
            ],
        ),
        actor,
    )
    assert result.failed == 1 and result.results[0].code == "VALIDATION_ERROR"
    assert "invalid-private-input" not in result.model_dump_json()
    existing.assert_not_awaited()
    session.rollback.assert_awaited_once()
