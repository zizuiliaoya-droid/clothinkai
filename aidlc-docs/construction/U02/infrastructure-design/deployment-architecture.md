# U02 部署架构（Deployment Architecture）

> 单元：U02 — 商品 / SKU 基础  
> 范围：U02 上线流程 + alembic migration 执行 + 验证清单 + 回滚预案  
> 通用部署架构见 `aidlc-docs/construction/U01/infrastructure-design/deployment-architecture.md`

---

## 1. 部署变更总览

### 1.1 服务变更

**无**。U01 已部署的 6 个 Zeabur 服务（frontend / backend / celery-worker / celery-beat / postgres / redis）配置不变：
- CPU / 内存 / replicas 不变
- 健康检查 `/health` + `/ready` 不变
- 环境变量不变（U02 不新增）
- Dockerfile 不变（U02 仅新增 Python 模块代码）

### 1.2 数据库变更（migration 004）

**新增**：
- 1 个 PostgreSQL 扩展（`pg_trgm`）
- 4 张表（brand / style / sku / style_detail_image）
- 12 个索引（含 1 个 GIN trgm）
- 4 条 RLS 策略

**migration 文件**：`backend/alembic/versions/004_u02_create_product_tables.py`

### 1.3 应用代码变更

**新增**：
- `backend/app/modules/product/`（service / repository / domain / api / schemas / models / permissions / exceptions / deps / legacy_field_permissions / brand_*）
- `backend/app/core/metrics.py` 追加 2 个自定义 Prometheus 指标
- `backend/app/core/exceptions.py` 追加 `FieldPermissionDenied`
- `frontend/src/features/product/`（U02 范围内仅最简骨架，详细 UI 由后续单元演进）

**修改**：
- `backend/app/main.py` 注册 product router
- `backend/app/core/permissions.py`（U01 已建）追加 `product:*` / `brand:*` permission 字符串
- `backend/app/modules/auth/default_roles.py` 追加 product / brand 默认角色映射

---

## 2. alembic migration 执行步骤

### 2.1 migration 内容

```python
# backend/alembic/versions/004_u02_create_product_tables.py
"""U02: create product tables (brand/style/sku/style_detail_image)

Revision ID: 004_u02
Revises: 003_u01_seed_initial_data
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from app.core.security.rls import rls_template


def upgrade():
    # 1. 启用 pg_trgm 扩展（trusted，clothing_bypass 角色可执行）
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    
    # 2. brand 表
    op.create_table(
        "brand",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_code", sa.String(32), nullable=False),
        sa.Column("brand_name", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
    )
    op.create_index("uq_brand_code", "brand", ["tenant_id", "brand_code"], unique=True)
    op.execute(rls_template("brand"))
    
    # 3. style 表
    op.create_table(
        "style",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_code", sa.String(64), nullable=False),
        sa.Column("style_name", sa.String(255), nullable=False),
        sa.Column("short_name", sa.String(64), nullable=True),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("season", sa.String(16), nullable=True),
        sa.Column("gender", sa.String(8), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tag_color", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("main_image_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("design_status", sa.String(16), nullable=False, server_default=sa.text("'大货'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["brand_id"], ["brand.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["main_image_id"], ["attachment.id"], ondelete="SET NULL"),
    )
    # 部分唯一（软删后释放）
    op.create_index(
        "uq_style_code", "style",
        ["tenant_id", "style_code"], unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index("idx_style_tenant_active", "style", ["tenant_id", "is_active", "is_deleted"])
    op.create_index("idx_style_brand", "style", ["tenant_id", "brand_id"])
    op.create_index("idx_style_category", "style", ["tenant_id", "category"])
    op.create_index("idx_style_owner", "style", ["tenant_id", "owner_id"])
    # GIN trgm 索引（U02 强制建）
    op.execute("""
        CREATE INDEX idx_style_search_trgm ON style
        USING gin (
            (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
            gin_trgm_ops
        ) WHERE is_deleted = false;
    """)
    op.execute(rls_template("style"))
    
    # 4. sku 表
    op.create_table(
        "sku",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_code", sa.String(64), nullable=False),
        sa.Column("color", sa.String(64), nullable=False),
        sa.Column("size", sa.String(32), nullable=False),
        sa.Column("cost_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("purchase_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("base_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("sourcing_type", sa.String(8), nullable=False, server_default=sa.text("'自产'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("cost_price >= 0", name="ck_sku_cost_price_nonneg"),
        sa.CheckConstraint("purchase_price >= 0", name="ck_sku_purchase_price_nonneg"),
        sa.CheckConstraint("base_price >= 0", name="ck_sku_base_price_nonneg"),
    )
    op.create_index(
        "uq_sku_code", "sku",
        ["tenant_id", "sku_code"], unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index("idx_sku_tenant_style", "sku", ["tenant_id", "style_id"])
    op.create_index("idx_sku_tenant_active", "sku", ["tenant_id", "is_active", "is_deleted"])
    op.create_index("idx_sku_style_active", "sku", ["tenant_id", "style_id", "is_active", "is_deleted"])
    op.execute(rls_template("sku"))
    
    # 5. style_detail_image 表
    op.create_table(
        "style_detail_image",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachment.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_sdi_style", "style_detail_image", ["tenant_id", "style_id", "sort_order"])
    op.execute(rls_template("style_detail_image"))


def downgrade():
    # 反向顺序删除（child → parent）
    op.drop_table("style_detail_image")
    op.drop_table("sku")
    op.drop_table("style")
    op.drop_table("brand")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")
```

### 2.2 migration 执行通道

**通过 `.github/workflows/migrate.yml` 专用 job**（与 U01 决策一致）：

```yaml
# .github/workflows/migrate.yml（U01 已建立，U02 不修改）
name: Migrate Database
on:
  workflow_dispatch:
    inputs:
      environment:
        type: choice
        options: [staging, production]
        required: true

jobs:
  migrate:
    environment: ${{ github.event.inputs.environment }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r backend/requirements.txt
      - name: Run alembic upgrade
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          BYPASS_DATABASE_URL: ${{ secrets.BYPASS_DATABASE_URL }}
        working-directory: backend
        run: alembic upgrade head
```

---

## 3. 部署流程

### 3.1 三阶段部署

```
┌────────────────────────────────────────────┐
│ Stage 1: PR 合并到 main 分支                │
│   - GitHub Actions ci.yml 自动触发          │
│   - 跑 lint + 单元测试 + 集成测试           │
│   - 必须全部 ✅ 才能 merge                  │
└─────────────────┬──────────────────────────┘
                  ▼
┌────────────────────────────────────────────┐
│ Stage 2: staging 环境 migration + 部署     │
│   2.1 手动触发 migrate.yml (env=staging)   │
│        → alembic upgrade 004_u02 在 staging│
│   2.2 验证 staging schema                   │
│   2.3 自动触发 deploy-staging.yml          │
│        → 部署 backend/frontend 等          │
│   2.4 staging 业务冒烟测试                  │
└─────────────────┬──────────────────────────┘
                  ▼
┌────────────────────────────────────────────┐
│ Stage 3: production 环境 migration + 部署  │
│   3.1 手动触发 migrate.yml (env=production)│
│   3.2 验证 production schema                │
│   3.3 触发 deploy-prod.yml                 │
│   3.4 production 业务验证                   │
└────────────────────────────────────────────┘
```

### 3.2 Stage 2 详细步骤（staging）

```bash
# Step 2.1: 触发 migrate.yml
gh workflow run migrate.yml -f environment=staging
# 等待 job 完成（约 30 秒）

# Step 2.2: 验证 schema（连接 staging DB）
psql $STAGING_DATABASE_URL -c '\dt'
# 期望输出：含 brand / style / sku / style_detail_image 4 行

psql $STAGING_DATABASE_URL -c '\d style'
# 期望输出：含 idx_style_search_trgm 索引 + RLS 已 enabled

psql $STAGING_DATABASE_URL -c "SELECT * FROM pg_extension WHERE extname='pg_trgm';"
# 期望输出：1 行

# Step 2.3: deploy-staging.yml 在 main 分支推送时自动触发
# 等待 Zeabur 完成部署（约 2 分钟）
curl https://staging.api.clothinkai.com/ready
# 期望输出：HTTP 200，{"checks": {"db": "ok", "redis": "ok"}}

# Step 2.4: 业务冒烟测试（自动 + 手动）
# 自动：跑 tests/integration/test_product_smoke.py
pytest tests/integration/test_product_smoke.py -v --base-url=https://staging.api.clothinkai.com

# 手动：UI 创建一个 brand → style → sku → match 接口
```

### 3.3 Stage 3 详细步骤（production）

与 Stage 2 相同，将 staging 替换为 production。**production 仅在 staging 全部 ✅ 后执行**。

---

## 4. 验证清单

### 4.1 Migration 后验证（执行在 staging / production）

| 检查项 | 命令 / 操作 | 期望 |
|---|---|---|
| 4 张新表存在 | `\dt brand style sku style_detail_image` | 4 张表 |
| pg_trgm 扩展已启用 | `SELECT extname FROM pg_extension WHERE extname='pg_trgm';` | 1 行 |
| GIN trgm 索引已创建 | `\d style` | 含 idx_style_search_trgm |
| RLS 已 enabled | `\d+ style` | "Row Security: enabled" |
| 部分唯一索引存在 | `SELECT indexname FROM pg_indexes WHERE indexname IN ('uq_style_code', 'uq_sku_code');` | 2 行 |
| 外键约束 | `\d sku` | 含 fk_sku_style / fk_sku_tenant |

### 4.2 应用部署后验证

| 检查项 | 命令 / 操作 | 期望 |
|---|---|---|
| 健康检查 | `curl https://api.clothinkai.com/ready` | 200 + db:ok + redis:ok |
| 新指标暴露 | `curl https://api.clothinkai.com/metrics | grep style_search` | 出现 style_search_results_count |
| Sentry 验证 | 触发一次 5xx 错误 | Sentry 收到事件，含 module=product tag |
| API 文档 | `curl https://api.clothinkai.com/api/docs` | 含 /api/styles/, /api/skus/ 端点 |

### 4.3 业务冒烟测试

```bash
# 1. 登录获取 JWT
TOKEN=$(curl -X POST https://api.clothinkai.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"..."}' | jq -r .access_token)

# 2. 创建 brand
BRAND_ID=$(curl -X POST https://api.clothinkai.com/api/brands/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"brand_code":"TESTBRAND","brand_name":"测试品牌"}' | jq -r .id)

# 3. 创建 style
STYLE_ID=$(curl -X POST https://api.clothinkai.com/api/styles/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"style_code\":\"TEST001\",\"style_name\":\"测试款式\",\"brand_id\":\"$BRAND_ID\",\"category\":\"连衣裙\"}" \
  | jq -r .id)

# 4. 创建 sku
curl -X POST https://api.clothinkai.com/api/skus/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"style_id\":\"$STYLE_ID\",\"sku_code\":\"TEST001-红-M\",\"color\":\"红\",\"size\":\"M\",\"cost_price\":100,\"sourcing_type\":\"自产\"}"

# 5. match 接口
curl "https://api.clothinkai.com/api/styles/match?style_code=TEST001" \
  -H "Authorization: Bearer $TOKEN"
# 期望返回：matched=true + style 信息

curl "https://api.clothinkai.com/api/styles/match?keyword=测试" \
  -H "Authorization: Bearer $TOKEN"
# 期望返回：candidates 数组含 TEST001
```

### 4.4 多租户隔离回归

```bash
# 用 tenant_a 用户登录创建 1 个 style
# 切换 tenant_b 用户登录
# 调用 GET /api/styles/ 不能看到 tenant_a 的 style
```

---

## 5. 回滚预案

### 5.1 回滚场景与动作

| 场景 | 动作 | 影响范围 |
|---|---|---|
| migrate.yml 失败（schema error） | alembic 自动 downgrade -1（migrate.yml 失败时不 commit）；应用层不部署 | 无（DB 状态保持上一版本） |
| migrate 成功但 deploy 失败（应用启动错误） | Zeabur 回滚到上一镜像；DB schema 仍为 004（兼容：U01 应用不引用 product 表） | DB 多了 4 张空表，无业务影响 |
| 业务冒烟失败（如 match 性能不达 SLA） | hotfix PR 调整 SQL → 重走 ci/migrate/deploy 流程；不回滚 schema | 影响 match 接口可用性，其他 OK |
| 数据腐败（极端） | `migrate.yml` 触发 downgrade（手动选 down）；从 R2 backups 恢复最近备份 | 业务回滚到备份时点 |

### 5.2 紧急回滚命令（人工）

```bash
# 回滚 migration（如必要）
gh workflow run migrate.yml -f environment=production -f action=downgrade

# Zeabur 镜像回滚（在 Zeabur 控制台操作）
# Service → Deployments → Rollback to previous

# 数据恢复（极端情况）
python backend/scripts/restore_backup.py \
  --backup-key daily/2026-05-25/daily-2026-05-25.tar.gz \
  --target-db production
```

---

## 6. 与共享基础设施的对齐

### 6.1 GitHub Actions 复用
- ci.yml：自动检查 PR，无需修改
- migrate.yml：执行 alembic upgrade 004_u02，无需修改
- deploy-staging.yml / deploy-prod.yml：自动触发，无需修改

### 6.2 R2 备份策略复用
- daily/monthly 备份自动包含 4 张新表（pg_dump 全库）
- 恢复演练通过 U01 已建立的 `backend/scripts/restore_backup.py`

### 6.3 Sentry 项目复用
- 后端异常自动捕获，含 module=product tag
- 不新建 Sentry 项目

---

## 7. 与下一阶段（Code Generation）的衔接

### 7.1 输入 Code Generation 的设计文档

| 文档 | 用途 |
|---|---|
| `U02/functional-design/domain-entities.md` | ORM 模型定义 |
| `U02/functional-design/business-rules.md` | service 层校验逻辑 |
| `U02/functional-design/business-logic-model.md` | API 端点流程 |
| `U02/nfr-requirements/nfr-requirements.md` | 性能 SLA / 测试覆盖率门槛 |
| `U02/nfr-requirements/tech-stack-decisions.md` | 索引 SQL / upsert 代码示例 |
| `U02/nfr-design/nfr-design-patterns.md` | 4 个 NFR 模式实现位置 |
| `U02/nfr-design/logical-components.md` | 25 新组件 + 4 层架构 |
| `U02/infrastructure-design/infrastructure-design.md` | 资源 / 索引 / RLS |
| `U02/infrastructure-design/deployment-architecture.md` | migration 文件 |

### 7.2 预期 Code Generation 产出

| 类别 | 文件数预估 |
|---|---|
| Backend 应用代码（modules/product/） | ~12 个文件 |
| Backend 修改（main.py / metrics.py / exceptions.py / default_roles.py） | 4 个文件修改 |
| alembic migration | 1 个文件（004_u02_create_product_tables.py） |
| 测试（unit / integration / api / performance） | ~10 个文件 |
| Frontend（最简骨架） | ~6 个文件 |
| 文档摘要（aidlc-docs/U02/code/） | 3 个文件 |

合计 ~36 个新文件 + 4 个修改文件。

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 部署服务无变更（6 服务配置不变） | ✅ |
| migration 走 migrate.yml 专用 job（与 U01 一致） | ✅ |
| Stage 1/2/3 部署流程清晰 | ✅ |
| 验证清单覆盖 4 类（schema / 应用 / 业务 / 多租户） | ✅ |
| 4 类回滚场景明确动作 | ✅ |
| 与 shared-infrastructure §5 / §9 完全对齐 | ✅ |
| 健康检查端点 /health + /ready 一致 | ✅ |
| Stage 2 staging 强制先验证 | ✅ |
