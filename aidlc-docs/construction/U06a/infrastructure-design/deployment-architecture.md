# U06a 部署架构（Deployment Architecture）

> 单元：U06a — 统一导入框架  
> 范围：migration 010 完整 DDL + 部署流程 + worker 验证 + 回滚预案  
> 关键：纯建表无 backfill；框架可独立部署（不依赖 U06b/c/d/e）

---

## 1. Migration 链总览

```
009_u05_seed_smoke_test_data         (U05 head)
        ↓
010_u06a_create_import_tables        (U06a — 3 表 + 约束 + 索引 + RLS + permission seed)
```

down_revision = `009_u05_seed_smoke_test_data`。纯建表 + seed，**无 backfill**，单独 `alembic upgrade head` 即可。

---

## 2. Migration 010 完整代码

```python
# backend/alembic/versions/010_u06a_create_import_tables.py
"""U06a - 创建统一导入框架表 + permission seed

Revision ID: 010_u06a_create_import_tables
Revises: 009_u05_seed_smoke_test_data
Create Date: 2026-06-03

包含：
- 3 表（import_batch / import_job / field_mapping）
- 约束：UNIQUE(tenant_id,source,file_hash) / UNIQUE(batch_id,row_number)
        / UNIQUE(tenant_id,source,version) / 部分 UNIQUE(tenant_id,source) WHERE is_active
- 索引 + 3 RLS 策略
- permission seed（importer.batch:read/write + importer.mapping:write，NF-5）
"""
from __future__ import annotations
from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import enable_rls_sql, disable_rls_sql

revision: str = "010_u06a_create_import_tables"
down_revision: str | Sequence[str] | None = "009_u05_seed_smoke_test_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── import_batch ──
    op.create_table(
        "import_batch",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_r2_key", sa.String(512), nullable=False),
        sa.Column("file_bucket", sa.String(16), nullable=False, server_default=sa.text("'private'")),
        sa.Column("mapping_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'processing'")),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("imported", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_import_batch_tenant"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL", name="fk_import_batch_created_by"),
        sa.CheckConstraint("status IN ('processing','completed','partial','failed')", name="ck_import_batch_status"),
        sa.CheckConstraint("file_bucket IN ('public','private','credentials','backups')", name="ck_import_batch_bucket"),
        sa.CheckConstraint("total_rows >= 0 AND imported >= 0 AND failed >= 0", name="ck_import_batch_counts_nonneg"),
        sa.CheckConstraint("retry_count >= 0 AND retry_count <= 3", name="ck_import_batch_retry"),
    )
    op.create_index("uq_import_batch_hash", "import_batch", ["tenant_id", "source", "file_hash"], unique=True)
    op.create_index("idx_import_batch_tenant_status", "import_batch", ["tenant_id", "status", sa.text("created_at DESC")])
    op.create_index("idx_import_batch_source", "import_batch", ["tenant_id", "source", sa.text("created_at DESC")])

    # ── import_job ──
    op.create_table(
        "import_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("target_resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_import_job_tenant"),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batch.id"], ondelete="CASCADE", name="fk_import_job_batch"),
        sa.CheckConstraint("status IN ('success','failed')", name="ck_import_job_status"),
        sa.CheckConstraint("attempt_count >= 1", name="ck_import_job_attempt"),
    )
    op.create_index("uq_import_job_batch_row", "import_job", ["batch_id", "row_number"], unique=True)
    op.create_index("idx_import_job_batch_status", "import_job", ["tenant_id", "batch_id", "status"])

    # ── field_mapping ──
    op.create_table(
        "field_mapping",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mapping_config", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_field_mapping_tenant"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL", name="fk_field_mapping_created_by"),
        sa.CheckConstraint("version >= 1", name="ck_field_mapping_version"),
    )
    op.create_index("uq_field_mapping_version", "field_mapping", ["tenant_id", "source", "version"], unique=True)
    op.create_index(
        "uq_field_mapping_active", "field_mapping", ["tenant_id", "source"],
        unique=True, postgresql_where=sa.text("is_active"),
    )
    op.create_index("idx_field_mapping_active", "field_mapping", ["tenant_id", "source", "is_active"])

    # ── RLS ──
    op.execute(enable_rls_sql("import_batch"))
    op.execute(enable_rls_sql("import_job"))
    op.execute(enable_rls_sql("field_mapping"))

    # ── permission seed（NF-5，幂等）──
    op.execute("""
DO $$
DECLARE
    v_perm RECORD;
    v_role RECORD;
    v_perm_id UUID;
BEGIN
    -- 3 个 scope → permission 表
    FOR v_perm IN SELECT * FROM (VALUES
        ('importer.batch:read', '导入批次读'),
        ('importer.batch:write', '导入批次写（上传/重试）'),
        ('importer.mapping:write', '字段映射写（管理员）')
    ) AS t(scope, descr) LOOP
        IF NOT EXISTS (SELECT 1 FROM permission WHERE scope = v_perm.scope) THEN
            INSERT INTO permission (id, scope, description, created_at, updated_at)
            VALUES (gen_random_uuid(), v_perm.scope, v_perm.descr, NOW(), NOW());
        END IF;
    END LOOP;

    -- role_permission 关联（按 default_roles.py NF-5）
    -- operations / admin / pr / pr_manager → batch:read
    -- admin / pr / pr_manager → batch:write
    -- admin / pr_manager → mapping:write
    FOR v_role IN SELECT * FROM (VALUES
        ('operations', 'importer.batch:read'),
        ('admin', 'importer.batch:read'),
        ('pr', 'importer.batch:read'),
        ('pr_manager', 'importer.batch:read'),
        ('admin', 'importer.batch:write'),
        ('pr', 'importer.batch:write'),
        ('pr_manager', 'importer.batch:write'),
        ('admin', 'importer.mapping:write'),
        ('pr_manager', 'importer.mapping:write')
    ) AS t(role_code, scope) LOOP
        SELECT id INTO v_perm_id FROM permission WHERE scope = v_role.scope;
        IF v_perm_id IS NOT NULL THEN
            INSERT INTO role_permission (role_id, permission_id)
            SELECT r.id, v_perm_id FROM role r
            WHERE r.code = v_role.role_code
              AND NOT EXISTS (
                  SELECT 1 FROM role_permission rp
                  WHERE rp.role_id = r.id AND rp.permission_id = v_perm_id
              );
        END IF;
    END LOOP;
END $$;
""")


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permission rp USING permission p
        WHERE rp.permission_id = p.id
          AND p.scope IN ('importer.batch:read','importer.batch:write','importer.mapping:write');
    """)
    op.execute("""
        DELETE FROM permission
        WHERE scope IN ('importer.batch:read','importer.batch:write','importer.mapping:write');
    """)
    op.execute(disable_rls_sql("field_mapping"))
    op.execute(disable_rls_sql("import_job"))
    op.execute(disable_rls_sql("import_batch"))
    op.drop_index("idx_field_mapping_active", table_name="field_mapping")
    op.drop_index("uq_field_mapping_active", table_name="field_mapping", postgresql_where=sa.text("is_active"))
    op.drop_index("uq_field_mapping_version", table_name="field_mapping")
    op.drop_table("field_mapping")
    op.drop_index("idx_import_job_batch_status", table_name="import_job")
    op.drop_index("uq_import_job_batch_row", table_name="import_job")
    op.drop_table("import_job")
    op.drop_index("idx_import_batch_source", table_name="import_batch")
    op.drop_index("idx_import_batch_tenant_status", table_name="import_batch")
    op.drop_index("uq_import_batch_hash", table_name="import_batch")
    op.drop_table("import_batch")
```

> 注：实际 permission / role_permission 表结构以 003_u01_seed_initial_data.py 为准，code gen 阶段对齐列名（scope / description / role_permission(role_id, permission_id)）。

---

## 3. 部署流程

```
1. PR 准备
   ├─ U06a 代码 + 010 migration + requirements(openpyxl) + nginx.conf(21m) 同 PR
   ├─ CI：lint + pytest（含 FakeImportAdapter 集成测试）+ grep autodiscover import_tasks
   └─ 合并 main

2. migration（migrate.yml，手动触发）
   ├─ alembic upgrade head（009 → 010）
   └─ 010 纯建表 + permission seed（无 backfill，幂等）

3. 镜像构建（含 openpyxl）
   └─ backend + celery-worker 同镜像重建（requirements 变更触发）

4. 部署（Zeabur 自动）
   ├─ backend redeploy（register_import_adapters in lifespan）
   ├─ celery-worker redeploy（worker_process_init 注册 + autodiscover import_tasks）
   ├─ frontend redeploy（nginx client_max_body_size 21m）
   └─ 注入 IMPORT_* env（可选，有默认）

5. 部署验证（见 §4）
```

---

## 4. Worker 验证（NF-4 关键）

部署后确认 worker 能找到任务 + 注册 Adapter：

```bash
# worker 日志应见
celery -A app.core.celery_app inspect registered | grep import.run_import_batch
# → 命中 = autodiscover import_tasks 生效（NF-4）

# 启动日志（worker_process_init）
# → "import_adapter_module_not_found" warning（U06b-e 未部署时正常）
# → 或 Adapter 注册成功日志（U06b-e 已部署）
```

- 框架独立部署（无 Adapter）：`ImportAdapterRegistry.sources()` 为空，upload 任何 source → 422，/health 正常（Q7）
- U06b 部署后：manual_style_sku 可用，再加 staging smoke

---

## 5. 部署前/后 checklist

### 5.1 部署前
- [ ] 010 migration + U06a 代码 + openpyxl + nginx 21m 同 PR
- [ ] CI pytest 全绿（FakeImportAdapter 集成测试）
- [ ] CI grep `app.tasks.import_tasks` 在 autodiscover 命中（NF-4）
- [ ] alembic upgrade head 在 staging 成功（009 → 010）

### 5.2 部署后
- [ ] worker `inspect registered` 含 import.run_import_batch（NF-4）
- [ ] `POST /api/import/upload`（无 Adapter 时）返回 422 IMPORT_SOURCE_UNKNOWN（框架健康）
- [ ] permission 表含 importer.batch:read/write + importer.mapping:write（NF-5）
- [ ] operations 角色可 GET /api/import/batches/（importer.batch:read 生效）
- [ ] 大文件（>21MB）被 nginx 拒（413，NF-6 L1）
- [ ] Sentry module=importer 无异常

---

## 6. 回滚预案

| 失败时机 | 处理 |
|---|---|
| 010 建表失败 | 修复 SQL 后重跑（CREATE TABLE 幂等性差，先 downgrade 010 再 upgrade） |
| permission seed 失败 | 幂等 NOT EXISTS，可重跑；或手动 downgrade 010 的 seed 段 |
| worker 找不到 run_import_batch | 检查 autodiscover import_tasks（NF-4）；hotfix celery_app |
| upload 全 422（registry 空） | 正常（U06b-e 未部署）；非故障 |
| 需整体回滚 | `alembic downgrade 009`（010 可逆：drop 3 表 + 移除 permission seed）。import 数据删除**不影响已入库业务记录**（adapter 写入各业务表独立存在，Q12）|

> import_batch / import_job 删除 ≠ 业务数据回滚：导入产生的 style/blogger/promotion/settlement 记录由各 adapter 写入对应业务表，独立于 import 框架表。R2 imports/ 文件回滚时一并清理（V1 脚本）。

---

## 7. CI 增量

```yaml
# ci.yml — 新增轻量检查（不阻塞框架部署）
- name: Verify import_tasks in Celery autodiscover (NF-4)
  run: |
    if ! grep -rn "app.tasks.import_tasks" backend/app/core/celery_app.py; then
      echo "::error::import_tasks not in autodiscover; run_import_batch.delay will fail"
      exit 1
    fi
    echo "OK: import_tasks autodiscovered"
```

- **不要求** Adapter 存在（框架可独立部署，Q8）
- MVP 不加导入 e2e-smoke（依赖 U06b-e，Q9）；U06b 部署后再加

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| migration 010 down_revision=009 + 纯建表无 backfill | ✅ §2 |
| 4 个 UNIQUE 约束（hash/row/version/active） | ✅ §2 |
| 3 RLS 策略 | ✅ §2 |
| permission seed importer.batch/mapping 幂等（NF-5） | ✅ §2 |
| worker 验证 autodiscover import_tasks（NF-4） | ✅ §4 |
| nginx 21m 部署验证（NF-6） | ✅ §5.2 |
| 010 可逆 + import 数据独立（Q12） | ✅ §6 |
| 框架可独立部署（Q7） | ✅ §4 §6 |
| CI grep autodiscover（不阻塞） | ✅ §7 |
