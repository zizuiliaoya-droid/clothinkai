"""U04 并发守护测试（FB2 + FB7）。

跨连接并发测试需要"真实已提交"数据对多个连接可见，故本文件不使用
rollback 隔离的 ``session`` fixture，而是用独立的 committed 数据（003 seed 的
默认 tenant）+ 显式清理（helper 见 ``tests/concurrency.py``）。并发度压到 30 以内
避免 PostgreSQL ``max_connections`` 上限。

- next_internal_sequence 30 并发首次创建 → 序号无重复
- update_state 30 并发 publish 同 promotion → 1 成功其余冲突
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.promotion.exceptions import StateTransitionConflictError
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.schemas import PromotionPublishRequest
from app.modules.promotion.service import PromotionService
from tests.concurrency import committed, default_tenant_id, run_concurrently


@pytest.mark.integration
@pytest.mark.asyncio
class TestSequenceConcurrent:
    """FB2: 首次创建 race 也无重复（INSERT ON CONFLICT DO UPDATE RETURNING）."""

    async def test_concurrent_first_create_no_duplicates(self, engine: Any) -> None:
        """30 并发同 (tenant_id, date_key) 首次序号 → 1..30 全部互不重复."""
        date_key = date(2026, 5, 27)
        tenant_id = await default_tenant_id(engine)

        # 前置清理（防上一轮残留导致起点 != 1）
        async with committed(engine) as pre:
            await pre.execute(
                text("DELETE FROM promotion_sequence " "WHERE tenant_id = :tid AND date_key = :dk"),
                {"tid": tenant_id, "dk": date_key},
            )

        async def fetch_one(s: AsyncSession, _i: int) -> int:
            repo = PromotionRepository(s)
            seq = await repo.next_internal_sequence(tenant_id=tenant_id, date_key=date_key)
            await s.commit()
            return seq

        try:
            results = await run_concurrently(engine, 30, fetch_one, tenant_id=tenant_id)
            assert len(set(results)) == 30, f"序号重复: {sorted(results)}"
            assert sorted(results) == list(range(1, 31))
        finally:
            async with committed(engine) as cleanup:
                await cleanup.execute(
                    text(
                        "DELETE FROM promotion_sequence "
                        "WHERE tenant_id = :tid AND date_key = :dk"
                    ),
                    {"tid": tenant_id, "dk": date_key},
                )


@pytest.mark.integration
@pytest.mark.asyncio
class TestPublishConcurrent:
    """FB7: UPDATE WHERE old_state RETURNING 保证只 1 个成功."""

    async def test_concurrent_publish_only_one_succeeds(
        self,
        engine: Any,
    ) -> None:
        """30 并发 publish 同 promotion → 1 成功其余冲突。

        全自包含 committed 数据（用 003 seed 的默认 tenant）+ finally 清理。
        """
        promotion_id = uuid4()
        style_id = uuid4()
        blogger_id = uuid4()
        user_id = uuid4()
        suffix = uuid4().hex[:8]
        tenant_id = await default_tenant_id(engine)

        async with committed(engine) as seed:
            role_row = (
                await seed.execute(text("SELECT id FROM role WHERE code = 'admin' LIMIT 1"))
            ).first()

            from app.core.security.auth import hash_password

            await seed.execute(
                text(
                    'INSERT INTO "user" (id, tenant_id, username, password_hash, '
                    "status, password_must_change, created_at, updated_at) "
                    "VALUES (:id, :tid, :un, :ph, 'active', false, NOW(), NOW())"
                ),
                {
                    "id": user_id,
                    "tid": tenant_id,
                    "un": f"conc_{suffix}",
                    "ph": hash_password("Password123"),
                },
            )
            if role_row is not None:
                await seed.execute(
                    text(
                        "INSERT INTO user_role (id, tenant_id, user_id, role_id) "
                        "VALUES (gen_random_uuid(), :tid, :uid, :rid)"
                    ),
                    {"tid": tenant_id, "uid": user_id, "rid": role_row[0]},
                )
            await seed.execute(
                text(
                    "INSERT INTO style (id, tenant_id, style_code, style_name, "
                    "category, design_status, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (:id, :tid, :code, 'CC款', '连衣裙', '大货', true, false, NOW(), NOW())"
                ),
                {"id": style_id, "tid": tenant_id, "code": f"CONCST{suffix}"},
            )
            await seed.execute(
                text(
                    "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, "
                    "platform, is_suspected_fake, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (:id, :tid, :xhs, 'CC博主', '小红书', false, true, false, NOW(), NOW())"
                ),
                {"id": blogger_id, "tid": tenant_id, "xhs": f"XHS{suffix}"},
            )
            await seed.execute(
                text(
                    "INSERT INTO promotion (id, tenant_id, style_id, blogger_id, pr_id, "
                    "internal_code, style_code_snapshot, style_short_name_snapshot, "
                    "quote_amount, platform, cooperation_date, publish_status, "
                    "recall_status, settlement_status, is_active, created_at, updated_at) "
                    "VALUES (:id, :tid, :sid, :bid, :uid, :code, 'SC', 'SN', 500.00, "
                    "'小红书', :cd, '未发布', '未召回', '未核查', true, NOW(), NOW())"
                ),
                {
                    "id": promotion_id,
                    "tid": tenant_id,
                    "sid": style_id,
                    "bid": blogger_id,
                    "uid": user_id,
                    "code": f"CONCPR{suffix}",
                    "cd": date(2026, 5, 26),
                },
            )
            # PRD 改动 5：publish 要求品牌词评论截图。直接插 attachment 行满足 FK，
            # 这里要测的是乐观并发，不是上传流程。
            bc_id = uuid4()
            await seed.execute(
                text(
                    "INSERT INTO attachment (id, tenant_id, bucket, r2_key, purpose, "
                    "filename, mime_type, size_bytes, status, created_at, updated_at) "
                    "VALUES (:id, :tid, 'private', :key, 'brand_comment_screenshot', "
                    "'bc.png', 'image/png', 128, 'ready', NOW(), NOW())"
                ),
                {"id": bc_id, "tid": tenant_id, "key": f"{tenant_id}/bc/{bc_id}.png"},
            )
            await seed.execute(
                text("UPDATE promotion SET brand_comment_attachment_id = :a WHERE id = :p"),
                {"a": bc_id, "p": promotion_id},
            )

        # 取 user ORM 对象供 service 使用
        from app.modules.auth.models import User

        async def attempt_publish(s: AsyncSession, _i: int) -> str:
            user = await s.get(User, user_id)
            svc = PromotionService(s)
            try:
                await svc.publish(
                    promotion_id,
                    PromotionPublishRequest(
                        publish_url="https://x.com/n",
                        actual_publish_date=date(2026, 5, 28),
                    ),
                    user,
                )
                return "ok"
            except StateTransitionConflictError:
                return "conflict"
            except Exception as e:
                return f"other:{type(e).__name__}"

        try:
            results = await run_concurrently(engine, 30, attempt_publish, tenant_id=tenant_id)
            ok_count = sum(1 for r in results if r == "ok")
            assert ok_count == 1, f"expected exactly 1 success, got {ok_count}; results: {results}"
        finally:
            async with committed(engine) as cleanup:
                await cleanup.execute(
                    text("DELETE FROM promotion WHERE id = :id"),
                    {"id": promotion_id},
                )
                await cleanup.execute(
                    text("DELETE FROM user_role WHERE user_id = :id"),
                    {"id": user_id},
                )
                await cleanup.execute(
                    text("DELETE FROM blogger WHERE id = :id"),
                    {"id": blogger_id},
                )
                await cleanup.execute(
                    text("DELETE FROM style WHERE id = :id"),
                    {"id": style_id},
                )
                await cleanup.execute(
                    text('DELETE FROM "user" WHERE id = :id'),
                    {"id": user_id},
                )
