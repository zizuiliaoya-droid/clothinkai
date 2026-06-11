# U03 部署架构（Deployment Architecture）

> 单元：U03 — 博主库基础  
> 范围：U03 上线流程 + alembic migration 005 + 验证清单 + 回滚预案  
> 通用部署架构见 U01 + U02 deployment-architecture.md

---

## 1. 部署变更总览

### 1.1 服务变更
**无**。U01 已部署的 6 个 Zeabur 服务配置不变（与 U02 部署一致）。

### 1.2 数据库变更（migration 005）

**新增**：
- 1 张表（blogger）
- 10 个索引（含 2 个 GIN trgm + 2 个 GIN JSONB）
- 1 条 RLS 策略

**migration 文件**：`backend/alembic/versions/005_u03_create_blogger_table.py`

注：pg_trgm 扩展已由 U02 migration 004 启用，本次幂等检查即可。

### 1.3 应用代码变更

**新增**：
- `backend/app/modules/blogger/`（service / repository / domain / api / schemas / models / permissions / exceptions / deps / legacy_field_permissions / enums，共 ~14 文件）
- `backend/app/core/metrics.py` 追加 1 个自定义 Prometheus 指标
- `frontend/src/features/blogger/`（最简骨架：types.ts + api.ts）

**修改**：
- `backend/app/main.py` 注册 blogger router

---

## 2. alembic migration 005 完整代码

```python
"""U03 - 创建博主库基础表 + GIN trgm + GIN JSONB + RLS

Revision ID: 005_u03_create_blogger_table
Revises: 004_u02_create_product_tables
Create Date: 2026-05-26 06:30:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "005_u03_create_blogger_table"
down_revision: str | Sequence[str] | None = "004_u02_create_product_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) 确保 pg_trgm 扩展已启用（U02 migration 004 已创建，幂等）
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # 2) blogger 表
    op.create_table(
        "blogger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("xiaohongshu_id", sa.String(64), nullable=False),
        sa.Column("nickname", sa.String(128), nullable=False),
        sa.Column(
            "platform",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'小红书'"),
        ),
        sa.Column("wechat", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("blogger_type", sa.String(16), nullable=True),
        sa.Column("gender_target", sa.String(16), nullable=True),
        sa.Column(
            "category_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "quality_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("quote", sa.Numeric(10, 2), nullable=True),
        sa.Column("cooperation_history", sa.Text(), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column(
            "is_suspected_fake",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_blogger_tenant"
        ),
        sa.CheckConstraint(
            "follower_count IS NULL OR follower_count >= 0",
            name="ck_blogger_follower_count_nonneg",
        ),
        sa.CheckConstraint(
            "quote IS NULL OR quote >= 0", name="ck_blogger_quote_nonneg"
        ),
    )

    # 3) 索引（10 个）
    # 部分唯一索引：软删后 xiaohongshu_id 释放
    op.create_index(
        "uq_blogger_xiaohongshu_id",
        "blogger",
        ["tenant_id", "xiaohongshu_id"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "idx_blogger_tenant_active",
        "blogger",
        ["tenant_id", "is_active", "is_deleted"],
    )
    op.create_index("idx_blogger_type", "blogger", ["tenant_id", "blogger_type"])
    op.create_index(
        "idx_blogger_follower_count", "blogger", ["tenant_id", "follower_count"]
    )
    op.create_index("idx_blogger_platform", "blogger", ["tenant_id", "platform"])
    op.create_index(
        "idx_blogger_suspected_fake",
        "blogger",
        ["tenant_id"],
        postgresql_where=sa.text("is_suspected_fake = true"),
    )

    # GIN trgm 单字段索引（U03 数据量小，不需要拼接表达式）
    op.execute(
        """
CREATE INDEX idx_blogger_nickname_trgm ON blogger
USING gin (nickname gin_trgm_ops) WHERE is_deleted = false;
""".strip()
    )
    op.execute(
        """
CREATE INDEX idx_blogger_xhs_id_trgm ON blogger
USING gin (xiaohongshu_id gin_trgm_ops) WHERE is_deleted = false;
""".strip()
    )

    # GIN JSONB 索引
    op.execute(
        "CREATE INDEX idx_blogger_category_tags ON blogger USING gin (category_tags);"
    )
    op.execute(
        "CREATE INDEX idx_blogger_quality_tags ON blogger USING gin (quality_tags);"
    )

    # 4) RLS 策略
    op.execute(enable_rls_sql("blogger"))


def downgrade() -> None:
    op.execute(disable_rls_sql("blogger"))

    op.execute("DROP INDEX IF EXISTS idx_blogger_quality_tags;")
    op.execute("DROP INDEX IF EXISTS idx_blogger_category_tags;")
    op.execute("DROP INDEX IF EXISTS idx_blogger_xhs_id_trgm;")
    op.execute("DROP INDEX IF EXISTS idx_blogger_nickname_trgm;")
    op.drop_index(
        "idx_blogger_suspected_fake",
        table_name="blogger",
        postgresql_where=sa.text("is_suspected_fake = true"),
    )
    op.drop_index("idx_blogger_platform", table_name="blogger")
    op.drop_index("idx_blogger_follower_count", table_name="blogger")
    op.drop_index("idx_blogger_type", table_name="blogger")
    op.drop_index("idx_blogger_tenant_active", table_name="blogger")
    op.drop_index(
        "uq_blogger_xiaohongshu_id",
        table_name="blogger",
        postgresql_where=sa.text("is_deleted = false"),
    )

    op.drop_table("blogger")

    # 不删除 pg_trgm 扩展（U02 仍在使用）
```

---

## 3. 部署流程

### 3.1 三阶段部署（与 U02 模式一致）

```
┌────────────────────────────────────────────┐
│ Stage 1: PR 合并到 main                    │
│   - GitHub Actions ci.yml 自动触发          │
│   - lint + 单元测试 + 集成测试              │
│   - 必须全部 ✅                              │
└─────────────────┬──────────────────────────┘
                  ▼
┌────────────────────────────────────────────┐
│ Stage 2: staging migration + 部署          │
│   2.1 触发 migrate.yml (env=staging)       │
│   2.2 验证 staging schema                   │
│   2.3 自动 deploy-staging.yml              │
│   2.4 业务冒烟测试                          │
└─────────────────┬──────────────────────────┘
                  ▼
┌────────────────────────────────────────────┐
│ Stage 3: production migration + 部署       │
│   重复 Stage 2 流程在 production            │
└────────────────────────────────────────────┘
```

### 3.2 Stage 2 详细命令

```bash
# Step 2.1: 触发 migrate.yml
gh workflow run migrate.yml -f environment=staging

# Step 2.2: 验证 schema
psql $STAGING_DATABASE_URL -c '\dt blogger'
# 期望：1 张 blogger 表

psql $STAGING_DATABASE_URL -c '\d blogger'
# 期望：10 个索引 + RLS enabled

psql $STAGING_DATABASE_URL -c "
EXPLAIN ANALYZE
SELECT id FROM blogger
WHERE tenant_id = '00000000-0000-0000-0000-000000000000'
  AND is_deleted = false
  AND is_active = true
  AND nickname ILIKE '%test%'
LIMIT 20;
"
# 期望：Bitmap Index Scan on idx_blogger_nickname_trgm

# Step 2.3: deploy-staging.yml 在 main 分支推送时自动触发
curl https://staging.api.clothinkai.com/ready
# 期望：HTTP 200

# Step 2.4: 业务冒烟（自动 + 手动）
pytest tests/integration/test_blogger_smoke.py --base-url=https://staging.api.clothinkai.com
```

### 3.3 Stage 3 — production
与 Stage 2 相同，将 staging 替换为 production。仅在 staging 全部 ✅ 后执行。

---

## 4. 验证清单

### 4.1 Migration 后验证

| 检查项 | 命令 | 期望 |
|---|---|---|
| blogger 表存在 | `\dt blogger` | 1 行 |
| pg_trgm 扩展启用 | `SELECT extname FROM pg_extension WHERE extname='pg_trgm';` | 1 行（U02 已启用） |
| GIN trgm 索引创建 | `\d blogger` | 含 idx_blogger_nickname_trgm + idx_blogger_xhs_id_trgm |
| GIN JSONB 索引创建 | `\d blogger` | 含 idx_blogger_category_tags + idx_blogger_quality_tags |
| RLS enabled | `\d+ blogger` | "Row Security: enabled" |
| partial UNIQUE 索引 | `SELECT indexname FROM pg_indexes WHERE indexname='uq_blogger_xiaohongshu_id';` | 1 行 |
| EXPLAIN 命中 GIN trgm | 见 §3.2 step 2.2 | Bitmap Index Scan |

### 4.2 应用部署后验证

| 检查项 | 命令 | 期望 |
|---|---|---|
| 健康检查 | `curl https://api.clothinkai.com/ready` | 200 + db:ok + redis:ok |
| 新指标暴露 | `curl https://api.clothinkai.com/metrics | grep blogger_search` | 出现 blogger_search_results_count |
| API 文档 | `curl https://api.clothinkai.com/api/docs` | 含 /api/bloggers/ 端点 |
| Sentry tag | 触发 5xx | 含 module=blogger tag |

### 4.3 业务冒烟测试

```bash
# 1. 登录获取 JWT
TOKEN=$(curl -X POST https://api.clothinkai.com/api/auth/login ... | jq -r .access_token)

# 2. 创建博主
BLOGGER_ID=$(curl -X POST https://api.clothinkai.com/api/bloggers/ \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"xiaohongshu_id":"TEST123","nickname":"测试博主","platform":"小红书"}' | jq -r .id)

# 3. 搜索博主
curl "https://api.clothinkai.com/api/bloggers/?keyword=测试" \
  -H "Authorization: Bearer $TOKEN"
# 期望：返回 BloggerPage 含 TEST123

# 4. 重复创建（应返回 409 + existing_blogger_id）
curl -X POST https://api.clothinkai.com/api/bloggers/ \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"xiaohongshu_id":"TEST123","nickname":"重复"}'
# 期望：409 + details.existing_blogger_id = $BLOGGER_ID
```

### 4.4 多租户隔离回归
```bash
# 用 tenant_a 创建博主
# 切换 tenant_b
# GET /api/bloggers/ 不能看到 tenant_a 的博主
```

---

## 5. 回滚预案

### 5.1 回滚场景

| 场景 | 动作 | 影响范围 |
|---|---|---|
| migrate.yml 失败（schema error） | alembic 自动 downgrade -1；应用层不部署 | 无 |
| migrate 成功但 deploy 失败 | Zeabur 回滚镜像；DB schema 仍为 005（兼容：U01/U02 不引用 blogger 表） | 多 1 张空表，无业务影响 |
| 业务冒烟失败 | hotfix PR → 重走流程；不回滚 schema | 影响 blogger 接口可用性 |
| 数据腐败（极端） | downgrade + 从 R2 backups 恢复 | 业务回滚 |

### 5.2 回滚命令

```bash
# 回滚 migration
gh workflow run migrate.yml -f environment=production -f action=downgrade

# Zeabur 镜像回滚（控制台操作）

# 数据恢复
python backend/scripts/restore_backup.py \
  --backup-key daily/2026-05-26/daily-2026-05-26.tar.gz \
  --target-db production
```

---

## 6. 与共享基础设施的对齐

| 资源 | 复用情况 |
|---|---|
| ci.yml / migrate.yml / deploy-* | 不修改，自动包含 005 migration |
| daily/monthly 备份 | blogger 表自动纳入 pg_dump |
| Sentry / Prometheus / Loki | 透明继承 |
| PostgreSQL 角色 | 不新增 |
| Celery 队列 | 不新增 |

---

## 7. 与 Code Generation 的衔接

### 7.1 输入 Code Generation 的设计文档
- U03 functional-design 3 文档
- U03 nfr-requirements 2 文档
- U03 nfr-design 2 文档
- U03 infrastructure-design 2 文档

### 7.2 预期 Code Generation 产出

| 类别 | 文件数预估 |
|---|---|
| Backend 业务代码（modules/blogger/） | ~14 个文件 |
| Backend 修改（main.py / metrics.py） | 2 个文件修改 |
| alembic migration | 1 个文件 |
| 测试（unit / integration / api / performance） | ~7 个文件 |
| Frontend 骨架（features/blogger/） | 2 个文件 |
| 文档摘要 | 3 个文件 |

合计 ~26 个新文件 + 2 个修改文件。

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 部署服务无变更（6 服务配置不变） | ✅ |
| migration 走 migrate.yml 专用 job | ✅ |
| 三阶段部署流程清晰（与 U02 一致） | ✅ |
| 验证清单覆盖 4 类（schema / 应用 / 业务 / 多租户） | ✅ |
| 4 类回滚场景明确 | ✅ |
| 与 shared-infrastructure 完全对齐 | ✅ |
| pg_trgm 扩展复用 U02（IF NOT EXISTS 幂等） | ✅ |
| Stage 2 staging 强制先验证 | ✅ |
