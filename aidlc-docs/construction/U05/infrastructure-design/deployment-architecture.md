# U05 部署架构（Deployment Architecture）

> 单元：U05 — 财务结款核心  
> 范围：007/008 migration 完整实施 + 双批部署流程 + 端到端 smoke + 回滚预案  
> 关键差异：**首次启用真实 e2e-smoke**（U04 batch 4 是 placeholder）

---

## 1. Migration 链总览

```
006_u04_create_promotion_tables.py    (U04 已部署)
        ↓
007_u05_create_settlement_tables.py   (U05 实施 — 创建表 + 索引 + RLS)
        ↓
008_u05_backfill_settlements.py       (U05 实施 — FB8 backfill PL/pgSQL)
```

**执行约束**：007 + 008 必须**一次性** `alembic upgrade head` 执行（不分两次），确保 007 创建表后立即 backfill。

---

## 2. Migration 007 完整代码

```python
# backend/alembic/versions/007_u05_create_settlement_tables.py
"""U05 - 创建财务结款核心表 + GIN trgm + RLS（永久 UNIQUE，FB3）

Revision ID: 007_u05_create_settlement_tables
Revises: 006_u04_create_promotion_tables
Create Date: 2026-05-26

包含内容：
- 3 张表（settlement / settlement_extra_item / settlement_sequence）
- 12 索引（含永久 UNIQUE，无 partial WHERE，FB3）
- 1 GIN trgm 索引（settlement_no）
- 2 RLS 策略（settlement / settlement_extra_item；settlement_sequence 不需要）
- 不修改 pg_trgm 扩展（U02 已启用）
"""

from __future__ import annotations
from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "007_u05_create_settlement_tables"
down_revision: str | Sequence[str] | None = "006_u04_create_promotion_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) settlement 表（注意：无 is_active 字段，FB3）
    op.create_table(
        "settlement",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("paid_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "payment_proof_attachment_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("settlement_no", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("payment_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("note_title", sa.String(255), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column(
            "settlement_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'待核查'"),  # FB1 起点
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_action", sa.String(16), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("request_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # FK
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_settlement_tenant"),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="RESTRICT", name="fk_settlement_promotion"),
        sa.ForeignKeyConstraint(["blogger_id"], ["blogger.id"], ondelete="RESTRICT", name="fk_settlement_blogger"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="RESTRICT", name="fk_settlement_style"),
        sa.ForeignKeyConstraint(["pr_id"], ["user.id"], ondelete="SET NULL", name="fk_settlement_pr"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"], ondelete="SET NULL", name="fk_settlement_reviewer"),
        sa.ForeignKeyConstraint(["paid_by"], ["user.id"], ondelete="SET NULL", name="fk_settlement_paid_by"),
        sa.ForeignKeyConstraint(
            ["payment_proof_attachment_id"], ["attachment.id"],
            ondelete="RESTRICT", name="fk_settlement_payment_proof",
        ),
        # CHECK
        sa.CheckConstraint("amount >= 0", name="ck_settlement_amount_nonneg"),
        sa.CheckConstraint("total_amount >= 0", name="ck_settlement_total_amount_nonneg"),
        sa.CheckConstraint(
            "payment_amount IS NULL OR payment_amount >= 0",
            name="ck_settlement_payment_amount_nonneg",
        ),
    )

    # 2) settlement 索引（11 个，含永久 UNIQUE，FB3）
    op.create_index("uq_settlement_no", "settlement", ["tenant_id", "settlement_no"], unique=True)
    op.create_index("uq_settlement_promotion", "settlement", ["tenant_id", "promotion_id"], unique=True)
    op.create_index("uq_settlement_request_event_id", "settlement", ["request_event_id"], unique=True)

    op.create_index("idx_settlement_tenant_status", "settlement",
                    ["tenant_id", "settlement_status", sa.text("created_at DESC")])
    op.create_index("idx_settlement_blogger", "settlement", ["tenant_id", "blogger_id"])
    op.create_index("idx_settlement_style", "settlement", ["tenant_id", "style_id"])
    op.create_index("idx_settlement_pr", "settlement", ["tenant_id", "pr_id"])
    op.create_index("idx_settlement_payment_date", "settlement", ["tenant_id", "payment_date"])
    op.create_index("idx_settlement_reviewed_by", "settlement", ["tenant_id", "reviewed_by"])
    op.create_index("idx_settlement_paid_by", "settlement", ["tenant_id", "paid_by"])

    # GIN trgm（settlement_no 关键字搜索；无 partial WHERE，FB3：所有 settlement 都活跃）
    op.execute(
        "CREATE INDEX idx_settlement_no_trgm ON settlement USING gin (settlement_no gin_trgm_ops);"
    )

    # 3) settlement_extra_item 表
    op.create_table(
        "settlement_extra_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("settlement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_type", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
                                name="fk_extra_item_tenant"),
        sa.ForeignKeyConstraint(["settlement_id"], ["settlement.id"], ondelete="CASCADE",
                                name="fk_extra_item_settlement"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL",
                                name="fk_extra_item_created_by"),
        sa.CheckConstraint("amount > 0", name="ck_extra_item_amount_pos"),
    )
    op.create_index("idx_extra_item_settlement", "settlement_extra_item",
                    ["tenant_id", "settlement_id"])

    # 4) settlement_sequence 表
    op.create_table(
        "settlement_sequence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date_key", sa.Date(), nullable=False),
        sa.Column(
            "last_seq", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
                                name="fk_settlement_sequence_tenant"),
        sa.CheckConstraint("last_seq >= 0", name="ck_settlement_sequence_nonneg"),
        sa.CheckConstraint("last_seq <= 9999", name="ck_settlement_sequence_max"),
    )
    op.create_index("uq_settlement_sequence", "settlement_sequence",
                    ["tenant_id", "date_key"], unique=True)

    # 5) RLS 策略（settlement_sequence 不启用 RLS）
    op.execute(enable_rls_sql("settlement"))
    op.execute(enable_rls_sql("settlement_extra_item"))


def downgrade() -> None:
    op.execute(disable_rls_sql("settlement_extra_item"))
    op.execute(disable_rls_sql("settlement"))

    op.drop_index("uq_settlement_sequence", table_name="settlement_sequence")
    op.drop_table("settlement_sequence")

    op.drop_index("idx_extra_item_settlement", table_name="settlement_extra_item")
    op.drop_table("settlement_extra_item")

    op.execute("DROP INDEX IF EXISTS idx_settlement_no_trgm;")
    op.drop_index("idx_settlement_paid_by", table_name="settlement")
    op.drop_index("idx_settlement_reviewed_by", table_name="settlement")
    op.drop_index("idx_settlement_payment_date", table_name="settlement")
    op.drop_index("idx_settlement_pr", table_name="settlement")
    op.drop_index("idx_settlement_style", table_name="settlement")
    op.drop_index("idx_settlement_blogger", table_name="settlement")
    op.drop_index("idx_settlement_tenant_status", table_name="settlement")
    op.drop_index("uq_settlement_request_event_id", table_name="settlement")
    op.drop_index("uq_settlement_promotion", table_name="settlement")
    op.drop_index("uq_settlement_no", table_name="settlement")
    op.drop_table("settlement")

    # 不删除 pg_trgm 扩展（U02 / U04 仍在使用）
```

---

## 3. Migration 008 完整代码（FB8 独立 backfill）

```python
# backend/alembic/versions/008_u05_backfill_settlements.py
"""U05 - 回填 U04 已审核但未创建 settlement 的历史数据（FB8 独立 migration）.

Revision ID: 008_u05_backfill_settlements
Revises: 007_u05_create_settlement_tables
Create Date: 2026-05-26

预期结果：U04+U05 同批部署 + FB1 强一致策略下，应补 0 行。
本 migration 处理边界场景（如 listener 注册晚于 review approve、监听器异常重启）。

幂等保证：NOT EXISTS 子句防重复（可重跑）。
不可逆：财务数据保护（downgrade 抛错）。
"""

from __future__ import annotations
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008_u05_backfill_settlements"
down_revision: str | Sequence[str] | None = "007_u05_create_settlement_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """通过 PL/pgSQL 复用 settlement_sequence 与 format_settlement_no 与正常路径一致."""
    op.execute("""
DO $$
DECLARE
    r RECORD;
    v_seq INTEGER;
    v_no TEXT;
    v_prefix TEXT;
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

        -- 2. 取 tenant_prefix（与 service 层 _get_tenant_code 一致）
        SELECT UPPER(LEFT(COALESCE(t.code, ''), 2)) INTO v_prefix
        FROM tenant t WHERE t.id = r.tenant_id;
        v_prefix := COALESCE(NULLIF(v_prefix, ''), 'XX');
        IF LENGTH(v_prefix) < 2 THEN
            v_prefix := RPAD(v_prefix, 2, 'X');
        END IF;

        -- 3. 生成 settlement_no（与 format_settlement_no 函数完全一致）
        v_no := v_prefix || 'S' || TO_CHAR(r.requested_at::date, 'YYMMDD') || LPAD(v_seq::TEXT, 4, '0');

        -- 4. INSERT settlement（settlement_status="待核查"，FB1 + FB2 与正常路径一致）
        INSERT INTO settlement (
            id, tenant_id, promotion_id, blogger_id, style_id, pr_id,
            settlement_no, amount, total_amount, settlement_status,
            request_event_id, note_title,
            created_at, updated_at
        ) VALUES (
            gen_random_uuid(),
            r.tenant_id,
            r.promotion_id,
            r.blogger_id,
            r.style_id,
            r.pr_id,
            v_no,
            r.amount,
            r.amount,  -- 初始无 extra_item
            '待核查',  -- FB1 + FB2 起点统一
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
            (SELECT id::text FROM settlement WHERE promotion_id = r.promotion_id LIMIT 1),
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
""")


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
```

---

## 4. 双批部署流程（继承 U04 + U05 启用 e2e-smoke）

### 4.1 标准流程（staging → production）

```
1. PR 准备
   ├─ U05 代码 + 007/008 migration 在同一 PR
   ├─ CI 跑 lint + unit + integration + grep finance.listeners 命中
   └─ 合并到 main

2. staging migration（手动触发）
   ├─ 触发 .github/workflows/migrate.yml(env=staging)
   ├─ alembic upgrade head（一次性升 007 + 008）
   └─ 008 backfill：FB1 强一致预期 0 行（验证 RAISE NOTICE 'created 0 settlement rows'）

3. staging deploy（自动触发，通过 staging 分支 push）
   ├─ Zeabur 自动 redeploy
   └─ 等待 /health 200

4. staging e2e-smoke（自动 — 本单元启用真实测试）
   ├─ 跑 e2e-smoke-after-deploy（详见 §5）
   ├─ 验证 FB1 强一致：U04 review approve → U05 settlement 创建 + status="待核查"
   └─ 失败 → 阻止 production 部署

5. production migration（手动触发）
   ├─ 触发 .github/workflows/migrate.yml(env=production)
   └─ alembic upgrade head

6. production deploy（自动触发，main 分支 push）
   ├─ Zeabur 自动 redeploy
   └─ 验证 /health + 抽样关键 API 响应正常
```

### 4.2 部署前验证 checklist

- [ ] U05 + 007 + 008 在同一 PR
- [ ] CI grep `from app.modules.finance.listeners import register` 命中
- [ ] alembic upgrade head 在 staging 成功 + 008 RAISE NOTICE 显示 0 行（FB1 验证）
- [ ] e2e-smoke-after-deploy 在 staging 通过（FB1 端到端验证）
- [ ] 抽样 `GET /api/settlements/daily-summary/as-of` 返回正常（FB7）
- [ ] 抽样 `POST /api/promotions/{id}/review action=approve` 返回 200 + settlement 已创建

### 4.3 部署后验证 checklist

- [ ] Sentry `module=finance` 无新异常
- [ ] Prometheus `settlement_created_via_event_total{result="error"}` rate = 0
- [ ] Prometheus `attachment_validation_failures_total{failure_type="tenant_mismatch"}` rate = 0
- [ ] PR 主管 / 财务 在 production 跑 dry-run（手动审核 1 个测试 settlement，全流程验证）

---

## 5. 端到端 smoke test 脚本（U04 batch 4 启用真实版本）

### 5.1 deploy-staging.yml::e2e-smoke-after-deploy 完整脚本

```yaml
# .github/workflows/deploy-staging.yml（U05 实施时更新 e2e-smoke 步骤）

  e2e-smoke-after-deploy:
    name: E2E smoke test (U04 + U05 J4 旅程)
    runs-on: ubuntu-latest
    needs: notify
    steps:
      - uses: actions/checkout@v4

      - name: Wait for staging deploy
        run: |
          for i in {1..30}; do
            if curl -fsS https://api-staging.clothinkai.com/health 2>/dev/null; then
              echo "::notice::Staging is healthy"
              break
            fi
            sleep 10
          done

      - name: Install jq + curl
        run: sudo apt-get install -y jq curl

      - name: Run J4 review approve smoke test
        env:
          API_BASE: https://api-staging.clothinkai.com
          SMOKE_USERNAME: smoke_test_pr_manager
          SMOKE_PASSWORD: ${{ secrets.STAGING_SMOKE_PR_MANAGER_PASSWORD }}
        run: |
          set -e
          
          # 1. 登录
          TOKEN=$(curl -sSf -X POST "$API_BASE/api/auth/login" \
            -H "Content-Type: application/json" \
            -d "{\"username\":\"$SMOKE_USERNAME\",\"password\":\"$SMOKE_PASSWORD\"}" \
            | jq -r .access_token)
          
          if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
            echo "::error::登录失败"
            exit 1
          fi
          
          # 2. 取一个 dummy promotion（pre-seeded by 009_seed_smoke_test_data.py，仅 staging）
          PROMOTION_ID=$(curl -sSf -H "Authorization: Bearer $TOKEN" \
            "$API_BASE/api/promotions/?publish_status=已发布&page_size=1" \
            | jq -r '.items[0].id')
          
          if [ -z "$PROMOTION_ID" ] || [ "$PROMOTION_ID" = "null" ]; then
            echo "::error::没有可用的 dummy promotion；请重新种子化 staging 测试数据"
            exit 1
          fi
          
          echo "::notice::Using promotion_id=$PROMOTION_ID"
          
          # 3. U04 review approve → 应该触发 SettlementRequested → U05 创建 settlement
          curl -sSf -X POST "$API_BASE/api/promotions/$PROMOTION_ID/review" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"action":"approve"}'
          
          echo "::notice::U04 review approve 成功"
          
          # 4. 验证 U05 端 settlement 已创建（FB1 强一致核心断言）
          SETTLEMENT_ID=$(curl -sSf -H "Authorization: Bearer $TOKEN" \
            "$API_BASE/api/settlements/?promotion_id=$PROMOTION_ID&page_size=1" \
            | jq -r '.items[0].id')
          
          if [ -z "$SETTLEMENT_ID" ] || [ "$SETTLEMENT_ID" = "null" ]; then
            echo "::error::FB1 强一致失败：U04 review approve 未创建 U05 settlement"
            exit 1
          fi
          
          echo "::notice::U05 settlement created: $SETTLEMENT_ID"
          
          # 5. 验证 settlement_status="待核查"（FB1 状态口径核心断言）
          STATUS=$(curl -sSf -H "Authorization: Bearer $TOKEN" \
            "$API_BASE/api/settlements/$SETTLEMENT_ID" \
            | jq -r .settlement_status)
          
          if [ "$STATUS" != "待核查" ]; then
            echo "::error::FB1 状态口径错误：起点应为'待核查'，实际为'$STATUS'"
            exit 1
          fi
          
          echo "::notice::settlement_status='待核查' 验证通过"
          
          # 6. cleanup：reject 推回避免污染
          curl -sSf -X PUT "$API_BASE/api/settlements/$SETTLEMENT_ID/review" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"action":"reject","review_reason":"smoke test cleanup"}'
          
          echo "::notice::Smoke test PASSED ✓"
```

### 5.2 测试数据准备（方案 A）

**Migration 009（仅 staging 跑）**：

```python
# backend/alembic/versions/009_u05_seed_smoke_test_data.py
"""U05 - staging 专用：种子化 smoke test 测试数据池.

仅 staging 环境运行（通过 alembic env.py 检查 ENVIRONMENT=staging）。
production 跳过此 migration。
"""

import os
import sqlalchemy as sa
from alembic import op

revision: str = "009_u05_seed_smoke_test_data"
down_revision: str | None = "008_u05_backfill_settlements"


def upgrade() -> None:
    if os.getenv("ENVIRONMENT") != "staging":
        return  # production 跳过
    
    # 创建 smoke_test_pr_manager 用户（如不存在）+ 10 个 dummy promotion (publish_status='已发布')
    # 详细 SQL 略，模式：
    # 1. INSERT INTO "user" smoke_test_pr_manager（hash 已知密码）
    # 2. 关联 pr_manager 角色
    # 3. INSERT 10 个 promotion（settlement_status='待核查' + publish_status='已发布'）
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM "user" WHERE username = 'smoke_test_pr_manager') THEN
                -- 创建 smoke 用户（密码由 GitHub Secret 注入，本 migration 仅设占位 hash）
                ...
            END IF;
            
            -- 补充 dummy promotion 池到 10 个
            IF (SELECT COUNT(*) FROM promotion WHERE remark = 'SMOKE_TEST_FIXTURE') < 10 THEN
                ...
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # 仅 staging 清理；production 无影响
    if os.getenv("ENVIRONMENT") != "staging":
        return
    op.execute("DELETE FROM promotion WHERE remark = 'SMOKE_TEST_FIXTURE';")
    op.execute("DELETE FROM \"user\" WHERE username = 'smoke_test_pr_manager';")
```

> V1 自动化：增加 Celery beat 任务每天补充 dummy promotion 到 10 个（防 smoke 用完池子）。

---

## 6. main.py register_event_listeners 双向扩展

```python
# backend/app/main.py（U04 batch 4 已搭框架，U05 实施时扩展第 2 步）

def register_event_listeners() -> None:
    """注册所有跨单元事件监听器。
    
    U05 实施时新增第 2 步加载 modules/promotion/listeners.py。
    """
    from app.core.events import clear_handlers
    
    clear_handlers()
    
    # 1. U05 → 监听 SettlementRequested（强一致正向，FB1）
    try:
        from app.modules.finance.listeners import register as register_finance  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        log.warning(
            "u05_finance_module_not_found_skipping_listener_registration. "
            "SettlementRequested events will fail with MissingRequiredHandlerError "
            "until U05 is deployed."
        )
        sentry_sdk.add_breadcrumb(
            message="U05 finance module not found",
            level="warning",
        )
        return  # finance 缺失时不继续注册 promotion listener
    
    try:
        register_finance()
    except Exception as exc:
        log.exception("listener_registration_failed", extra={"module": "finance"})
        raise RuntimeError(
            "U05 finance listener registration failed, refusing to start"
        ) from exc
    
    # 2. U04 → 监听 SettlementPaid（通知类反向，FB5）
    try:
        from app.modules.promotion.listeners import register as register_promotion_listeners  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        # 通知类容忍：缺失只 warning，不阻塞
        log.warning(
            "u04_promotion_listeners_module_not_found_skipping. "
            "SettlementPaid events will be dropped (acceptable; required_handler=False)."
        )
        return
    
    try:
        register_promotion_listeners()
    except Exception as exc:
        # 模块存在但内部错误：fail fast
        log.exception("listener_registration_failed", extra={"module": "promotion"})
        raise RuntimeError(
            "U04 promotion listener registration failed, refusing to start"
        ) from exc
```

---

## 7. 回滚预案

### 7.1 008 backfill 失败

| 失败时机 | 处理 | 影响 |
|---|---|---|
| 008 部分执行后崩溃 | 重跑 008（NOT EXISTS 幂等保护，无重复风险） | 极小 — 部分 settlement 已创建，再跑只补缺失 |
| 008 完全失败（内部 SQL 错误） | 修复 SQL 后重跑 008 | 7 已升 + 008 未跑：新数据走正常路径，无影响 |
| 错误数据已 INSERT | admin 通过 audit_log（action='settlement.create_via_backfill'）反查 + 手动审计后清理 | 需 admin 介入 |

**绝不通过 downgrade 回滚** — 008 downgrade 抛 RuntimeError（财务数据保护）。

### 7.2 007 失败

| 失败时机 | 处理 |
|---|---|
| 007 创建表失败 | 修复 SQL 后重跑（CREATE TABLE 幂等性差，需先 downgrade 007 再 upgrade） |
| 007 已升但 008 未跑 | 安全状态 — 应用启动后 SettlementRequested 会创建 settlement（正常路径），008 留空 |

### 7.3 应用启动失败

| 失败原因 | 处理 |
|---|---|
| register_finance 失败（U05 模块存在但内部 import 错误） | container restart loop → 立即修复代码 + redeploy |
| register_finance 失败（U05 模块不存在） | 不可能 — CI grep 应已阻止；若发生则 container restart loop + 紧急 hotfix |
| register_promotion_listeners 失败（U04 模块存在但错误） | container restart loop → 修复（V1 评估改为 warning） |

### 7.4 e2e-smoke 失败

| 失败原因 | 处理 |
|---|---|
| FB1 强一致失败（settlement 未创建） | 阻止 production 部署；检查 register_finance 是否成功 + listener 是否生效 |
| FB1 状态口径错误（status != 待核查） | 阻止 production 部署；检查 SettlementRequested handler 代码 + DB DEFAULT |
| 网络超时 | 重试 1 次；仍失败则阻止部署 + 排查 staging 健康 |

### 7.5 反向事件 SettlementPaid 失败

- **FB5 通知类容忍**：单次失败不影响主流程（mark_paid 已成功）
- 监控指标 `settlement_paid_sync_no_match_total` rate > 5/min → Sentry warning
- V1 reconcile 任务每天凌晨 03:00 补齐
- MVP 阶段允许短暂不一致，以 settlement 为 source of truth

---

## 8. 监控仪表盘（V1 实施）

MVP 阶段复用 U01 通用 Grafana 仪表盘（已包含所有 Prometheus 指标）。

V1 新增专属仪表盘：
- **Finance Settlement Dashboard**：
  - 各 settlement_status 分布饼图
  - 平均结算周期（创建 → 已付款）线图
  - 跨租户 attachment 告警热力图
- **Reverse Sync Monitoring Dashboard**：
  - settlement_paid_sync_no_match_total 累计趋势
  - reconcile 任务执行频次 / 处理行数

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 007 创建 3 表 + 12 索引 + 2 RLS（永久 UNIQUE，FB3） | ✅ |
| 008 backfill PL/pgSQL 复用 settlement_sequence + format_settlement_no（FB8） | ✅ |
| 008 状态写"待核查"（FB1 + FB2 与正常路径一致） | ✅ |
| 008 downgrade 抛错（财务数据保护） | ✅ |
| 008 IF NOT EXISTS 幂等可重跑 | ✅ |
| e2e-smoke 启用真实端到端（验证 FB1） | ✅ |
| production 部署强约束（staging smoke 失败阻止） | ✅ |
| main.py 双向 listener 注册 + 失败处理不对称 | ✅ |
| 测试数据策略（staging 专用 dummy 池） | ✅ |
| 回滚预案覆盖 4 类失败场景 | ✅ |
| Sentry 告警路由（跨租户警告 → 后端 + 安全 leader） | ✅ |
| Prometheus 5 个指标 + 6 类告警阈值 | ✅ |
