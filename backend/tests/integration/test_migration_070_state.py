import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_claim_schema_and_default_preparation_state(session, tenant_a):
    assert (
        await session.scalar(
            text("SELECT blogger_claims_enabled_at FROM tenant WHERE id=:id"), {"id": tenant_a.id}
        )
    ) is None
    assert tuple(
        (
            await session.execute(
                text(
                    "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='blogger_claim'::regclass"
                )
            )
        ).one()
    ) == (True, True)
    assert await session.scalar(
        text(
            "SELECT has_table_privilege('clothing_app','blogger_claim','SELECT,INSERT,UPDATE,DELETE')"
        )
    )
    index = await session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname='uq_blogger_claim'")
    )
    assert "UNIQUE" in index and "(tenant_id, blogger_id, pr_id)" in index
    references = (
        await session.scalars(
            text(
                "SELECT conname FROM pg_constraint WHERE conrelid='blogger_claim'::regclass AND contype='f'"
            )
        )
    ).all()
    assert {
        "fk_claim_blogger_tenant",
        "fk_claim_pr_tenant",
        "fk_claim_actor_tenant",
        "fk_claim_work_tenant",
    }.issubset(references)
    assert (
        await session.scalar(
            text(
                "SELECT is_nullable FROM information_schema.columns WHERE table_name='import_batch' AND column_name='claim_work_wechat_id'"
            )
        )
        == "YES"
    )
