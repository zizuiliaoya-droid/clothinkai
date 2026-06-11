## U04 部署架构（Deployment Architecture）

> 单元：U04 — 推广合作核心  
> 关键约束：**U04 必须与 U05 同批部署**（FB1）

---

## 1. 部署变更总览

### 1.1 服务变更
**无**。U01 已部署的 6 个 Zeabur 服务配置不变。

### 1.2 数据库变更（migration 005_u04）
- 创建 2 张表：promotion / promotion_sequence
- 创建 13 个索引
- 启用 1 条 RLS 策略
- 追加 promotion.review:approve 细粒度权限 + 角色绑定

### 1.3 应用代码变更
**新增**：
- `backend/app/modules/promotion/`（13 个文件：models/schemas/enums/permissions/legacy_field_permissions/legacy_settings/exceptions/domain/repository/service/state_machines/events/api/deps + urge_calculator/metrics_calculator）
- `backend/app/core/events.py`（新建事件总线 — 由 U04 发起）
- `backend/app/core/metrics.py` 追加 4 个指标
- `frontend/src/features/promotion/`（types.ts + api.ts 骨架）

**修改**：
- `backend/app/main.py` 注册 promotion router + lifespan 钩子调用 register_event_listeners
- `backend/app/core/state_machine.py` 扩展 assert_can_transition / get_allowed_transitions classmethod
- `.github/workflows/ci.yml` 追加 validate-listeners job
- `.github/workflows/deploy-staging.yml` 追加 e2e-smoke-after-deploy step


---

## 2. alembic migration 005_u04 关键代码

```python
"""U04 - 创建 promotion / promotion_sequence 表 + GIN trgm + RLS

Revision ID: 005_u04_create_promotion_tables
Revises: 004_u02_create_product_tables (note: U03 是 005，本单元改为 006，最终 ID 由实际顺序决定)
Create Date: 2026-05-27 06:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision = "005_u04_create_promotion_tables"  # 注：实际 revision id 顺序按 alembic chain 调整
down_revision = "..."  # 上一 migration


def upgrade() -> None:
    # 1. promotion 表
    op.create_table(
        "promotion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("internal_code", sa.String(64), nullable=False),
        sa.Column("style_code_snapshot", sa.String(64), nullable=False),
        sa.Column("style_short_name_snapshot", sa.String(128), nullable=False),
        sa.Column("quote_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("cost_snapshot", sa.Numeric(10, 2), nullable=True),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("cooperation_date", sa.Date(), nullable=False),
        sa.Column("scheduled_publish_date", sa.Date(), nullable=True),
        sa.Column("actual_publish_date", sa.Date(), nullable=True),
        sa.Column("publish_url", sa.String(512), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("recall_reason", sa.Text(), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=True),
        sa.Column("note_title", sa.String(255), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("publish_status", sa.String(16), nullable=False, server_default=sa.text("'未发布'")),
        sa.Column("recall_status", sa.String(16), nullable=False, server_default=sa.text("'未召回'")),
        sa.Column("settlement_status", sa.String(16), nullable=False, server_default=sa.text("'未核查'")),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_action", sa.String(16), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["blogger_id"], ["blogger.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pr_id"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"], ondelete="SET NULL"),
        sa.CheckConstraint("quote_amount >= 0", name="ck_promotion_quote_amount_nonneg"),
        sa.CheckConstraint("cost_snapshot IS NULL OR cost_snapshot >= 0", name="ck_promotion_cost_nonneg"),
        sa.CheckConstraint("like_count IS NULL OR like_count >= 0", name="ck_promotion_like_count_nonneg"),
    )

    # 2. 13 个索引（含 GIN trgm）
    op.create_index("uq_promotion_internal_code", "promotion", ["tenant_id", "internal_code"], unique=True)
    op.create_index("idx_promotion_tenant_active", "promotion", ["tenant_id", "is_active", "publish_status"])
    op.create_index("idx_promotion_pr", "promotion", ["tenant_id", "pr_id"])
    op.create_index("idx_promotion_blogger", "promotion", ["tenant_id", "blogger_id", "publish_status"])
    op.create_index("idx_promotion_style", "promotion", ["tenant_id", "style_id", "publish_status"])
    op.create_index("idx_promotion_cooperation_date", "promotion", ["tenant_id", sa.text("cooperation_date DESC")])
    op.create_index("idx_promotion_settlement_status", "promotion", ["tenant_id", "settlement_status"])
    op.create_index("idx_promotion_recall_status", "promotion", ["tenant_id", "recall_status"])
    op.create_index("idx_promotion_publish_dates", "promotion", ["tenant_id", "publish_status", "scheduled_publish_date"])

    op.execute("""
        CREATE INDEX idx_promotion_internal_code_trgm ON promotion
        USING gin (internal_code gin_trgm_ops) WHERE is_active = true;
    """)
    op.execute("""
        CREATE INDEX idx_promotion_style_code_snapshot_trgm ON promotion
        USING gin (style_code_snapshot gin_trgm_ops) WHERE is_active = true;
    """)
    op.execute("""
        CREATE INDEX idx_promotion_short_name_trgm ON promotion
        USING gin (style_short_name_snapshot gin_trgm_ops) WHERE is_active = true;
    """)

    op.execute(enable_rls_sql("promotion"))

    # 3. promotion_sequence 表
    op.create_table(
        "promotion_sequence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date_key", sa.Date(), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
    )
    op.create_index("uq_promotion_sequence", "promotion_sequence", ["tenant_id", "date_key"], unique=True)
    op.execute(enable_rls_sql("promotion_sequence"))

    # 4. permission seed 增量
    op.execute("""
        INSERT INTO permission (id, scope, name, category, created_at, updated_at)
        VALUES 
            (gen_random_uuid(), 'promotion.review', 'promotion 审核', 'function', NOW(), NOW()),
            (gen_random_uuid(), 'promotion.review:approve', 'promotion 审核-批准', 'function', NOW(), NOW())
        ON CONFLICT (scope) DO NOTHING;
    """)
    op.execute("""
        INSERT INTO role_permission (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id
        FROM role r, permission p
        WHERE r.code IN ('admin', 'platform_admin', 'pr_manager')
          AND p.scope = 'promotion.review:approve'
        ON CONFLICT (role_id, permission_id) DO NOTHING;
    """)


def downgrade() -> None:
    # 反向顺序删除
    op.execute("""
        DELETE FROM role_permission
        WHERE permission_id IN (
            SELECT id FROM permission 
            WHERE scope IN ('promotion.review', 'promotion.review:approve')
        );
    """)
    op.execute("""
        DELETE FROM permission 
        WHERE scope IN ('promotion.review', 'promotion.review:approve');
    """)
    op.execute(disable_rls_sql("promotion_sequence"))
    op.drop_table("promotion_sequence")
    op.execute(disable_rls_sql("promotion"))
    op.execute("DROP INDEX IF EXISTS idx_promotion_short_name_trgm;")
    op.execute("DROP INDEX IF EXISTS idx_promotion_style_code_snapshot_trgm;")
    op.execute("DROP INDEX IF EXISTS idx_promotion_internal_code_trgm;")
    # ... drop 其他 B-tree 索引
    op.drop_table("promotion")
```


---

## 3. 三阶段部署流程（含 U04+U05 同批强约束）

```
┌──────────────────────────────────────────────────────────┐
│ Stage 1: PR 合并 main                                    │
│   - U04 + U05 必须在同 PR（或 U05 PR 先合）              │
│   - ci.yml 自动跑：lint + test + validate-listeners      │
│   - validate-listeners 失败 → PR 阻塞                     │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ Stage 2: staging migration + 部署                         │
│   2.1 触发 migrate.yml(env=staging)                       │
│        → alembic upgrade head（一次升 005_u04 + 006_u05） │
│   2.2 验证 staging schema                                 │
│   2.3 自动 deploy-staging.yml                             │
│   2.4 e2e-smoke-after-deploy job 强制运行                  │
│        → test_review_approve_creates_settlement.py        │
│        → 失败禁 production 部署                            │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│ Stage 3: production migration + 部署                      │
│   重复 Stage 2 流程在 production                          │
└──────────────────────────────────────────────────────────┘
```

### 3.1 Stage 2 详细命令

```bash
# Step 2.1
gh workflow run migrate.yml -f environment=staging
# 等待完成（30-60 秒）

# Step 2.2 验证 schema
psql $STAGING_DATABASE_URL -c '\dt'
# 期望：含 promotion / promotion_sequence

psql $STAGING_DATABASE_URL -c '\d promotion'
# 期望：13 个索引 + RLS enabled + 28 字段

psql $STAGING_DATABASE_URL -c "
EXPLAIN ANALYZE
SELECT id FROM promotion
WHERE tenant_id = '00000000-0000-0000-0000-000000000000'
  AND is_active = true
  AND publish_status = '未发布'
  AND scheduled_publish_date - DATE '2026-05-27' > 10
LIMIT 20;
"
# 期望：Bitmap Index Scan on idx_promotion_publish_dates

# Step 2.3 deploy-staging.yml 在 main 分支推送时自动触发
# Step 2.4 自动 e2e smoke 后置验证
```

---

## 4. 验证清单

### 4.1 Migration 后验证

| 检查项 | 期望 |
|---|---|
| `\dt promotion promotion_sequence` | 2 张表 |
| `\d promotion` 索引 | 13 个含 GIN trgm |
| `\d+ promotion` RLS | enabled + force |
| `SELECT scope FROM permission WHERE scope LIKE 'promotion.review%'` | 2 行 |
| `SELECT 1 FROM role_permission WHERE ...` | admin/pr_manager 有绑定 |
| EXPLAIN ANALYZE urge_status 查询 | 命中 idx_promotion_publish_dates |

### 4.2 应用部署后验证

| 检查项 | 期望 |
|---|---|
| `curl /ready` | 200 + db:ok |
| `curl /metrics | grep promotion_state` | 出现 4 个新指标 |
| Sentry tag | `module=promotion` |
| OpenAPI | 11 个 promotion 端点 |
| **register_event_listeners 启动日志** | 无 ModuleNotFoundError warning |

### 4.3 E2E Smoke Test（强制）

```python
# tests/e2e/test_review_approve_creates_settlement.py
@pytest.mark.e2e
async def test_review_approve_creates_settlement_via_event(api_client, ...):
    """端到端：U04 review approve → SettlementRequested 事件 → U05 创建 settlement."""
    # 1. 创建 promotion
    # 2. publish
    # 3. PR 主管 review approve
    # 4. 断言 settlement 表新增 1 条记录（U05 监听器创建）
    # 5. 断言 promotion.settlement_status = "待付款"
    ...
```

### 4.4 多租户隔离回归
```bash
# tenant_a 创建 promotion，tenant_b 看不到
```

---

## 5. 回滚预案

| 场景 | 动作 | 影响范围 |
|---|---|---|
| migrate 失败（schema error） | alembic 自动 downgrade -1 | 无 |
| migrate 成功但 deploy 失败 | Zeabur 镜像回滚；schema 仍为 005+006 | 多 2 张空表 |
| **e2e smoke 失败** | 阻止 production 部署；查 register_event_listeners 启动日志；修复 PR | staging 影响业务但不影响 production |
| 业务 bug | hotfix PR + 重走流程；不回滚 schema | 部分功能 |

### 5.1 紧急回滚命令

```bash
# 回滚 migration（极端）
gh workflow run migrate.yml -f environment=production -f action=downgrade

# Zeabur 镜像回滚（控制台）

# 数据恢复
python backend/scripts/restore_backup.py --backup-key daily/2026-05-27/...
```

---

## 6. 与 Code Generation 的衔接

### 6.1 输入 Code Generation 的设计文档
- U04 functional-design 3 文档
- U04 nfr-requirements 2 文档（含 8 P1 反馈修正）
- U04 nfr-design 2 文档（含 8 P1 反馈修正）
- U04 infrastructure-design 2 文档

### 6.2 预期 Code Generation 产出

| 类别 | 文件数预估 |
|---|---|
| Backend 业务代码（modules/promotion/） | ~16 个文件 |
| Backend 修改（main.py / metrics.py / state_machine.py / events.py 新建） | 3 modified + 1 new |
| Alembic migration 005 | 1 |
| 测试套件（unit/integration/api/performance） | ~12 文件 |
| Frontend 骨架 | 2 |
| 文档摘要 | 3 |
| CI/CD 修改 | ci.yml + deploy-staging.yml |

合计 ~35 新文件 + 5 修改 + 2 CI/CD 修改。

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 部署服务无变更 | ✅ |
| migration 走 migrate.yml 专用 job | ✅ |
| **U04+U05 同批部署强约束（5 层防护）** | ✅ |
| 三阶段部署流程清晰 | ✅ |
| 验证清单覆盖 4 类（schema / 应用 / 业务 / e2e smoke） | ✅ |
| 4 类回滚场景明确 | ✅ |
| 与 shared-infrastructure 完全对齐 | ✅ |
