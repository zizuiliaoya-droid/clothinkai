"""U05 - 回填 U04 已审核但未创建 settlement 的历史数据（FB8 独立 migration）.

Revision ID: 008_u05_backfill_settlements
Revises: 007_u05_create_settlement_tables
Create Date: 2026-05-26

预期结果：U04+U05 同批部署 + FB1 强一致策略下，应补 0 行。
本 migration 处理边界场景（如 listener 注册晚于 review approve、监听器异常重启）。

幂等保证：NOT EXISTS 子句防重复（可重跑）。
不可逆：财务数据保护（downgrade 抛错）。

复用正常路径生成逻辑（FB8）：
- settlement_sequence INSERT ON CONFLICT DO UPDATE RETURNING（与 next_settlement_sequence 一致）
- settlement_no 格式与 format_settlement_no 完全一致（<prefix>S<yyMMdd><0001>）
- settlement_status='待核查'（FB1 + FB2 与正常事件路径起点统一）
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "008_u05_backfill_settlements"
down_revision: str | Sequence[str] | None = "007_u05_create_settlement_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """通过 PL/pgSQL 复用 settlement_sequence 与 format_settlement_no 与正常路径一致."""
    op.execute(
        r"""
DO $$
DECLARE
    r RECORD;
    v_seq INTEGER;
    v_no TEXT;
    v_prefix TEXT;
    v_settlement_id UUID;
    v_count INTEGER := 0;
BEGIN
    -- 扫描待回填 promotion（FB1 强一致下应为 0）
    FOR r IN
        SELECT
            p.id AS promotion_id,
            p.tenant_id,
            p.blogger_id,
            p.style_id,
            p.pr_id,
            p.quote_amount AS amount,
            p.note_title,
            COALESCE(p.reviewed_at, p.updated_at, NOW()) AS requested_at,
            p.reviewed_by AS requested_by
        FROM promotion p
        WHERE p.settlement_status = '待付款'
          AND p.is_active = true
          AND NOT EXISTS (
              SELECT 1 FROM settlement s WHERE s.promotion_id = p.id
          )
        ORDER BY p.tenant_id, COALESCE(p.reviewed_at, p.updated_at, NOW())
    LOOP
        -- 1. 通过 settlement_sequence 分配下一序列号（与 next_settlement_sequence 完全一致）
        INSERT INTO settlement_sequence
            (id, tenant_id, date_key, last_seq, created_at, updated_at)
        VALUES (gen_random_uuid(), r.tenant_id, r.requested_at::date, 1, NOW(), NOW())
        ON CONFLICT (tenant_id, date_key) DO UPDATE
        SET last_seq = settlement_sequence.last_seq + 1, updated_at = NOW()
        RETURNING last_seq INTO v_seq;

        IF v_seq > 9999 THEN
            RAISE EXCEPTION 'backfill: settlement_sequence overflow for tenant_id=% date=%',
                r.tenant_id, r.requested_at::date;
        END IF;

        -- 2. 取 tenant_prefix（与 format_settlement_no 一致：前 2 字符大写，不足补 X）
        SELECT UPPER(LEFT(COALESCE(t.code, ''), 2)) INTO v_prefix
        FROM tenant t WHERE t.id = r.tenant_id;
        v_prefix := COALESCE(NULLIF(v_prefix, ''), 'XX');
        IF LENGTH(v_prefix) < 2 THEN
            v_prefix := RPAD(v_prefix, 2, 'X');
        END IF;

        -- 3. 生成 settlement_no（与 format_settlement_no 函数完全一致：<prefix>S<yyMMdd><0001>）
        v_no := v_prefix || 'S' || TO_CHAR(r.requested_at::date, 'YYMMDD') || LPAD(v_seq::TEXT, 4, '0');

        -- 4. INSERT settlement（settlement_status='待核查'，FB1 + FB2 与正常路径一致）
        v_settlement_id := gen_random_uuid();
        INSERT INTO settlement (
            id, tenant_id, promotion_id, blogger_id, style_id, pr_id,
            settlement_no, amount, total_amount, settlement_status,
            request_event_id, note_title,
            created_at, updated_at
        ) VALUES (
            v_settlement_id,
            r.tenant_id,
            r.promotion_id,
            r.blogger_id,
            r.style_id,
            r.pr_id,
            v_no,
            r.amount,
            r.amount,        -- 初始无 extra_item
            '待核查',         -- FB1 + FB2 起点统一
            gen_random_uuid(),  -- 合成 event_id（无原始事件可追溯）
            r.note_title,
            NOW(),
            NOW()
        );

        v_count := v_count + 1;

        -- 5. 写 audit 留痕
        INSERT INTO audit_log (
            id, tenant_id, user_id, actor_type, action, resource, resource_id,
            after, created_at
        ) VALUES (
            gen_random_uuid(),
            r.tenant_id,
            r.requested_by,
            'system_migration',
            'settlement.create_via_backfill',
            'settlement',
            v_settlement_id::text,
            jsonb_build_object(
                'settlement_no', v_no,
                'promotion_id', r.promotion_id::text,
                'amount_changed', true,
                'total_amount_changed', true,
                'settlement_status', '待核查',
                'backfill_migration', '008'
            ),
            NOW()
        );
    END LOOP;

    RAISE NOTICE 'backfill: created % settlement rows', v_count;
END $$;
"""
    )


def downgrade() -> None:
    """财务数据保护：不可逆.

    若需回滚，admin 手动审计 audit_log 中 action='settlement.create_via_backfill'
    的所有 settlement 行后清理。
    """
    raise RuntimeError(
        "backfill migration 008 is not reversible. "
        "To recover, query audit_log WHERE action='settlement.create_via_backfill' "
        "and manually clean up affected settlement rows after audit review."
    )
