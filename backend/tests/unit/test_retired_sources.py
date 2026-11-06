"""R14: reject new source input without touching storage or database."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.importer.adapters.huitun import HuitunImportAdapter
from app.modules.importer.exceptions import ImportSourceRetiredError
from app.modules.importer.service import ImportService


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["huitun", "huitun_douyin"])
@pytest.mark.parametrize("crawler", [False, True])
async def test_retired_upload_has_no_side_effects(source, crawler):
    session = MagicMock()
    storage = MagicMock()
    svc = ImportService(session, attachment_service=storage)
    storage.reset_mock()  # constructor checks whether a storage override was supplied
    kwargs = {
        "content": b"test",
        "filename": "history.csv",
        "content_type": "text/csv",
        "source": source,
    }
    with pytest.raises(ImportSourceRetiredError) as exc:
        if crawler:
            await svc.upload_for_crawler(**kwargs, tenant_id=uuid4())
        else:
            await svc.upload(**kwargs, user=SimpleNamespace(id=uuid4(), tenant_id=uuid4()))
    assert exc.value.status_code == 422
    assert exc.value.details == {"source": source, "retryable": False}
    assert not session.mock_calls
    assert not storage.mock_calls


def test_old_batch_profile_does_not_turn_missing_values_into_zero():
    adapter = HuitunImportAdapter()
    profile = adapter._build_profile(
        adapter.parse_row({"小红书ID": "old", "平均阅读": "100"}, None)
    )
    assert profile["note_stats"]["avg_likes"] is None
    assert profile["note_stats"]["avg_reads"] == 100


@pytest.mark.asyncio
async def test_schedule_skips_retired_credentials():
    from app.modules.collect.crawler_task_service import CrawlerTaskService

    result = MagicMock()
    result.scalars.return_value.all.return_value = [SimpleNamespace(platform="灰豚")]
    session = AsyncMock()
    session.execute.return_value = result
    assert await CrawlerTaskService(session).schedule_for_tenant(uuid4()) == 0
    assert session.execute.await_count == 1  # read credentials, never INSERT a task


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["huitun", "huitun_douyin"])
async def test_historical_batch_remains_readable_and_retryable(source, monkeypatch):
    from app.core.security.permissions import EffectivePermissions
    from app.tasks.import_tasks import run_import_batch

    session = AsyncMock()
    svc = ImportService(session)
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    batch = SimpleNamespace(
        id=uuid4(), tenant_id=user.tenant_id, source=source, retry_count=1, failed=1
    )
    svc._repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=batch), claim_for_retry=AsyncMock(return_value=batch)
    )
    svc._audit = SimpleNamespace(log=AsyncMock())
    enqueue = MagicMock()
    monkeypatch.setattr(run_import_batch, "apply_async", enqueue)
    perms = EffectivePermissions(user_id=str(user.id), scopes=frozenset({"*"}))
    assert await svc.get_batch(batch.id, user, perms) is batch
    assert await svc.retry(batch.id, user, perms) is batch
    session.commit.assert_awaited_once()
    enqueue.assert_called_once_with(args=[str(batch.id)], kwargs={"only_failed": True}, countdown=1)
