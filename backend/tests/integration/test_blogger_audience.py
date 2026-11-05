"""PostgreSQL：画像局部更新、旧日期归档与整组导入事务。"""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.core.tenancy import tenant_id_ctx
from app.modules.blogger.audience_models import BloggerAudienceCapture as Capture
from app.modules.blogger.audience_schemas import AudienceInput
from app.modules.blogger.audience_service import list_audience, save_audience
from app.modules.importer.adapters.blogger_audience import BloggerAudienceImportAdapter
from app.modules.importer.adapters.huitun import HuitunImportAdapter
from app.modules.importer.models import ImportJob
from app.modules.importer.registry import ImportAdapterRegistry

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a):
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


def payload(**changes):
    return AudienceInput.model_validate(
        {
            "dimension": "age",
            "collected_on": "2026-11-05",
            "source": "来源截图",
            "completeness": "partial",
            "estimated": False,
            "entries": [{"label": "41–50", "percentage": 23.5}],
            **changes,
        }
    )


@pytest.mark.usefixtures("tenant_ctx")
async def test_dimension_dates_idempotency_history_and_legacy_preservation(
    session, tenant_a, tenant_b, blogger_factory, flow_users
):
    blogger = await blogger_factory.blogger()
    blogger.audience_profile = {"note_stats": {"avg_likes": 0, "avg_reads": 100}, "legacy": "keep"}
    first = await save_audience(session, tenant_a.id, blogger.id, payload(), flow_users.pr.id)
    assert first.inserted and first.current
    same = await save_audience(session, tenant_a.id, blogger.id, payload(), flow_users.pr.id)
    assert same.id == first.id and not same.inserted
    with pytest.raises(ValidationError, match="不同内容"):
        await save_audience(
            session, tenant_a.id, blogger.id, payload(source="冲突"), flow_users.pr.id
        )
    older = await save_audience(
        session, tenant_a.id, blogger.id, payload(collected_on="2026-10-01"), flow_users.pr.id
    )
    assert not older.current and "归档" in older.message
    await save_audience(
        session,
        tenant_a.id,
        blogger.id,
        payload(dimension="gender", entries=[{"label": "女", "percentage": 0}]),
        flow_users.pr.id,
    )
    current = await list_audience(session, tenant_a.id, blogger.id)
    assert {item.dimension for item in current.items} == {"age", "gender"}
    assert next(item for item in current.items if item.dimension == "age").id == first.id
    assert (await list_audience(session, tenant_a.id, blogger.id, history=True)).total == 3
    assert (
        blogger.audience_profile["note_stats"]["avg_likes"] == 0
        and blogger.audience_profile["legacy"] == "keep"
    )
    await HuitunImportAdapter().upsert(
        {
            "xiaohongshu_id": blogger.xiaohongshu_id,
            "audience_profile_raw": '{"structured_v1":{"fake":true},"note_stats":{"avg_likes":12}}',
        },
        session=session,
        tenant_id=tenant_a.id,
        actor_id=flow_users.pr.id,
    )
    assert (await list_audience(session, tenant_a.id, blogger.id)).total == 2
    with pytest.raises(ResourceNotFoundError):
        await list_audience(session, tenant_b.id, blogger.id)
    with pytest.raises(ResourceNotFoundError):
        await save_audience(session, tenant_b.id, blogger.id, payload(), None)


@pytest.mark.usefixtures("tenant_ctx")
async def test_runner_group_atomicity_row_errors_and_retry(
    session, tenant_a, blogger_factory, import_batch_factory, flow_users, monkeypatch
):
    from app.core.attachment import attachment_service
    from app.tasks import import_tasks as tasks

    blogger = await blogger_factory.blogger()
    batch = await import_batch_factory.batch(
        source="manual_blogger_audience", created_by=flow_users.pr.id
    )
    content = (
        "平台,账号,维度,原始标签,占比,采集日期,采集来源,完整性,是否估计\n"
        f"{blogger.platform},{blogger.xiaohongshu_id},年龄,41–50,60,2026-11-05,来源截图,完整,否\n"
        f"{blogger.platform},{blogger.xiaohongshu_id},年龄,50+,bad,2026-11-05,来源截图,完整,否\n"
        f"{blogger.platform},{blogger.xiaohongshu_id},性别,女,0,2026-11-05,来源截图,部分,否\n"
    ).encode()
    factory = async_sessionmaker(
        bind=session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    @asynccontextmanager
    async def sessions():
        async with factory() as fresh:
            yield fresh

    monkeypatch.setattr(tasks, "AsyncSessionApp", sessions)
    monkeypatch.setattr(tasks, "AsyncSessionBypass", sessions)
    monkeypatch.setattr(attachment_service, "get_object_bytes", lambda *args: content)
    monkeypatch.setitem(
        ImportAdapterRegistry._adapters, "manual_blogger_audience", BloggerAudienceImportAdapter()
    )
    result = await tasks._run_import_batch(batch.id)
    assert result == {"status": "partial", "imported": 1, "failed": 2}
    jobs = (
        await session.scalars(
            select(ImportJob).where(ImportJob.batch_id == batch.id).order_by(ImportJob.row_number)
        )
    ).all()
    assert [job.status for job in jobs] == ["failed", "failed", "success"]
    assert all("第 2 数据行" in job.error_detail for job in jobs[:2])
    assert (
        await session.scalars(select(Capture).where(Capture.blogger_id == blogger.id))
    ).one().dimension == "gender"
    jobs[1].raw_data = {**jobs[1].raw_data, "占比": "40"}
    batch.status = "processing"
    await session.flush()
    retried = await tasks._run_import_batch(batch.id, only_failed=True)
    assert retried["status"] == "completed"
    # 手工与导入同一payload校验和幂等契约。
    same = await save_audience(
        session,
        tenant_a.id,
        blogger.id,
        payload(
            completeness="complete",
            entries=[{"label": "41–50", "percentage": 60}, {"label": "50+", "percentage": 40}],
        ),
        flow_users.pr.id,
    )
    assert not same.inserted
    record = await session.get(Capture, same.id)
    assert record.import_batch_id == batch.id and record.row_numbers == [1, 2]


@pytest.mark.usefixtures("tenant_ctx")
async def test_group_rolls_back_capture_when_second_job_write_fails(
    session, tenant_a, blogger_factory, import_batch_factory, flow_users, monkeypatch
):
    from app.modules.importer.audience_runner import process_audience_groups
    from app.tasks import import_tasks as tasks

    blogger = await blogger_factory.blogger()
    batch = await import_batch_factory.batch(source="manual_blogger_audience")
    factory = async_sessionmaker(
        bind=session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    monkeypatch.setattr(tasks, "AsyncSessionApp", factory)
    monkeypatch.setattr(tasks, "AsyncSessionBypass", factory)
    original = tasks._upsert_job

    async def fail_second(*args, **kwargs):
        if kwargs["status"] == "success" and kwargs["row_number"] == 2:
            raise RuntimeError("第二行结果写入失败")
        return await original(*args, **kwargs)

    monkeypatch.setattr(tasks, "_upsert_job", fail_second)
    raw = {
        "平台": blogger.platform,
        "账号": blogger.xiaohongshu_id,
        "维度": "年龄",
        "采集日期": "2026-11-05",
        "采集来源": "来源截图",
        "完整性": "完整",
        "是否估计": "否",
    }
    outcomes = await process_audience_groups(
        [
            (1, {**raw, "原始标签": "41–50", "占比": "60"}),
            (2, {**raw, "原始标签": "50+", "占比": "40"}),
        ],
        None,
        batch_id=batch.id,
        tenant_id=tenant_a.id,
        actor_id=flow_users.pr.id,
        app_session=factory,
        bypass_session=factory,
    )
    assert outcomes == [None, None]
    assert not (
        await session.scalars(select(Capture).where(Capture.blogger_id == blogger.id))
    ).all()
    jobs = (await session.scalars(select(ImportJob).where(ImportJob.batch_id == batch.id))).all()
    assert len(jobs) == 2 and all(job.status == "failed" for job in jobs)


@pytest.mark.usefixtures("tenant_ctx")
async def test_capture_rls_hides_other_tenant_and_rejects_reassignment(
    session, tenant_a, tenant_b, blogger_factory, flow_users
):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    blogger = await blogger_factory.blogger()
    own = await save_audience(session, tenant_a.id, blogger.id, payload(), flow_users.pr.id)
    other = await blogger_factory.blogger(tenant=tenant_b)
    token = tenant_id_ctx.set(tenant_b.id)
    try:
        foreign = await save_audience(session, tenant_b.id, other.id, payload(), None)
    finally:
        tenant_id_ctx.reset(token)
    await session.execute(text("SET LOCAL ROLE clothing_app"))
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_a.id)}
    )
    await session.execute(text("SELECT set_config('app.bypass_rls', 'off', true)"))
    try:
        assert (await session.execute(text("SELECT current_user"))).scalar_one() == "clothing_app"
        visible = (
            (
                await session.execute(
                    text("SELECT id FROM blogger_audience_capture WHERE id IN (:own, :foreign)"),
                    {"own": own.id, "foreign": foreign.id},
                )
            )
            .scalars()
            .all()
        )
        assert visible == [own.id]
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin_nested():
                # 通过原生SQL验证新表WITH CHECK，而不借服务层租户判断。
                await session.execute(
                    text("UPDATE blogger_audience_capture SET tenant_id = :other WHERE id = :own"),
                    {"other": tenant_b.id, "own": own.id},
                )
    finally:
        await session.execute(text("RESET ROLE"))
