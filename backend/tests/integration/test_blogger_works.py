"""实际 PostgreSQL：作品快照、逐行导入与租户边界。"""

from contextlib import asynccontextmanager
from datetime import date

import pytest
from sqlalchemy import select

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.core.tenancy import tenant_id_ctx
from app.modules.blogger.work_models import BloggerWorkSnapshot
from app.modules.blogger.work_schemas import WorkInput
from app.modules.blogger.work_service import list_works, save_work
from app.modules.importer.adapters.blogger_work import BloggerWorkImportAdapter
from app.modules.importer.models import ImportJob
from app.modules.importer.registry import ImportAdapterRegistry

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a):
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


def payload(**changes):
    return WorkInput.model_validate(
        {
            "work_id": "work-1",
            "published_on": "2026-11-01",
            "collected_at": "2026-11-05 14:30:00",
            "source": "平台人工采集",
            "like_count": 123,
            **changes,
        }
    )


@pytest.mark.usefixtures("tenant_ctx")
async def test_snapshots_latest_idempotent_conflict_and_tenant(
    session, tenant_a, tenant_b, blogger_factory, flow_users
):
    blogger = await blogger_factory.blogger()
    original, inserted = await save_work(
        session, tenant_a.id, blogger.id, payload(), flow_users.pr.id
    )
    assert inserted
    repeated, inserted = await save_work(
        session, tenant_a.id, blogger.id, payload(), flow_users.pr.id
    )
    assert not inserted and repeated.id == original.id
    with pytest.raises(ValidationError, match="不同内容"):
        await save_work(session, tenant_a.id, blogger.id, payload(like_count=999), flow_users.pr.id)
    newer, _ = await save_work(
        session,
        tenant_a.id,
        blogger.id,
        payload(collected_at="2026-11-06 14:30:00", like_count=456),
        flow_users.pr.id,
    )
    await save_work(
        session,
        tenant_a.id,
        blogger.id,
        payload(collected_at="2026-11-04 14:30:00", like_count=None),
        flow_users.pr.id,
    )
    current = await list_works(session, tenant_a.id, blogger.id)
    assert current.total == 1 and current.items[0].id == newer.id
    history = await list_works(session, tenant_a.id, blogger.id, snapshot_id=original.id)
    assert history.total == 3 and history.items[0].like_count == 456
    assert (
        await list_works(session, tenant_a.id, blogger.id, date_from=date(2026, 11, 2))
    ).total == 0
    with pytest.raises(ResourceNotFoundError):
        await list_works(session, tenant_b.id, blogger.id)
    with pytest.raises(ResourceNotFoundError):
        await save_work(session, tenant_b.id, blogger.id, payload(), None)


@pytest.mark.usefixtures("tenant_ctx")
async def test_real_runner_partial_duplicate_and_manual_equivalence(
    session, tenant_a, blogger_factory, import_batch_factory, flow_users, monkeypatch
):
    import app.tasks.import_tasks as tasks
    from app.core.attachment import attachment_service

    blogger = await blogger_factory.blogger()
    batch = await import_batch_factory.batch(
        source="manual_blogger_work", created_by=flow_users.pr.id
    )
    line = f"{blogger.platform},{blogger.xiaohongshu_id},work-1,2026-11-01,2026-11-05 14:30:00,平台人工采集,123\n"
    content = (
        "平台,账号,作品ID,发布日期,采集时间,采集来源,点赞\n"
        + line
        + line
        + line.replace(",123\n", ",999\n")
        + line.replace(blogger.xiaohongshu_id, "missing_account")
    ).encode()

    @asynccontextmanager
    async def sessions():
        yield session

    monkeypatch.setattr(tasks, "AsyncSessionApp", sessions)
    monkeypatch.setattr(tasks, "AsyncSessionBypass", sessions)
    monkeypatch.setattr(attachment_service, "get_object_bytes", lambda *args: content)
    monkeypatch.setitem(
        ImportAdapterRegistry._adapters, "manual_blogger_work", BloggerWorkImportAdapter()
    )
    result = await tasks._run_import_batch(batch.id, only_failed=False)
    assert result["status"] == "partial"
    jobs = (
        await session.scalars(
            select(ImportJob).where(ImportJob.batch_id == batch.id).order_by(ImportJob.row_number)
        )
    ).all()
    assert [job.status for job in jobs] == ["success", "skipped", "failed", "failed"]
    assert "不同内容" in jobs[2].error_detail
    assert "未匹配" in jobs[3].error_detail
    row = (
        await session.scalars(
            select(BloggerWorkSnapshot).where(BloggerWorkSnapshot.blogger_id == blogger.id)
        )
    ).one()
    repeated, inserted = await save_work(
        session, tenant_a.id, blogger.id, payload(), flow_users.pr.id
    )
    assert not inserted and repeated.id == row.id
    assert row.import_batch_id == batch.id and row.row_number == 1
