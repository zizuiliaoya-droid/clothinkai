# 共享基础设施（Shared Infrastructure）

> **本文档由 U01 创建**，作为所有后续单元（U02-U18）共享的基础设施基线。每个单元在自己的 Infrastructure Design 中"引用"本文档，只描述**自己新增**的部分。

---

## 1. 基础设施所有权

| 资源 | 拥有者单元 | 后续单元如何使用 |
|---|---|---|
| GitHub 仓库 `clothinkai/clothing-erp` | U01 | 直接 PR |
| Zeabur production 项目 | U01 | 在已有项目内增加新服务/调整配置 |
| Zeabur staging 项目 | U01 | 同上 |
| PostgreSQL 16 实例 | U01 | 通过 Alembic migration 加表/索引 |
| Redis 实例 + DB 分片 | U01 | 用现有 DB 0/1/2/3，新单元不创建新 DB |
| Cloudflare R2 4 个桶 | U01 | 在桶内加新子目录（如 `imports/` `attachments/{tenant_id}/promotions/`） |
| Sentry 2 个项目 | U01 | 复用 DSN，加新 tag/transaction |
| 域名 + DNS + TLS | U01 | 不变 |
| GitHub Actions workflows | U01 | 后续单元只加新 step 或新 workflow，不重写基础结构 |

---

## 2. 关键 ID / URL / Secret 名称（用于跨单元引用）

> 实际值在 Zeabur Secrets / Cloudflare 控制台 / Sentry 控制台中管理。本文档**只列名称**，避免泄露。

### 2.1 Zeabur 项目
- production project ID：`<填入>`
- staging project ID：`<填入>`
- 服务 ID 在 Zeabur 控制台查询

### 2.2 域名
- prod 前端：`https://app.clothinkai.com`
- prod 后端：`https://api.clothinkai.com`
- staging 前端：`https://staging.app.clothinkai.com`
- staging 后端：`https://staging.api.clothinkai.com`

### 2.3 Cloudflare R2
- Endpoint URL：`https://<account-id>.r2.cloudflarestorage.com`
- 桶名：
  - `clothing-erp-public`
  - `clothing-erp-private`
  - `clothing-erp-credentials`
  - `clothing-erp-backups`

### 2.4 Sentry
- Backend 项目名：`clothing-erp-backend`
- Frontend 项目名：`clothing-erp-frontend`

### 2.5 PostgreSQL Roles
- `clothing_app`：业务主连接（启用 RLS）
- `clothing_bypass`：系统任务/平台管理员（绕过 RLS）
- `clothing_archiver`：audit_log 归档专用

---

## 3. 共享环境变量清单

> 所有 backend / celery-worker / celery-beat 服务都已注入这些变量。新单元的 Infrastructure Design 只列**额外**变量。

详见 `U01/infrastructure-design/infrastructure-design.md` 第 10 节。

---

## 4. 共享设计模式

### 4.1 多租户隔离
- ORM Session 自动注入 tenant_id（U01 实现的 core/db.py + core/tenancy.py）
- PostgreSQL RLS（U01 启用，所有 TenantScopedModel 表自动应用策略）
- 所有新表必须继承 `TenantScopedModel` 基类，自动获得 tenant_id 字段 + 唯一约束模板 + RLS 策略

### 4.2 审计日志
- @audit("operation_name") 装饰器（U01 实现）
- AuditService.log() 显式调用
- ORM 事件钩子自动监听敏感表

### 4.3 状态机
- `core/state_machine.py` StateMachine 基类（U01 实现）
- 各模块在 `domain.py` 中声明 transition_table

### 4.4 附件
- AttachmentService（U01 实现）
- 业务表用 `attachment_id` 关联，不直接存 URL

### 4.5 分页 / 排序 / 错误码
- 统一 `Page<T>` 响应模型（U01 实现）
- 错误码格式 `{ code, message, details }`（U01 实现）

---

## 5. 共享数据库迁移规约

### 5.1 命名约定
`{NNN}_{unit-id}_{description}.py`，如：
- `001_u01_initial_schema.py`
- `002_u01_enable_rls.py`
- `003_u01_seed_initial_data.py`
- `004_u02_add_style_sku.py`
- `005_u02_seed_default_categories.py`

### 5.2 必须可回滚
每个 migration 的 downgrade() 必须可执行（即使是 NotImplementedError 也比留空好）。

### 5.3 RLS 启用模板
新增 `TenantScopedModel` 表时调用 `core/security/rls.py` 的辅助函数生成策略 SQL：

```python
# alembic/versions/XXX_u02_add_style.py
def upgrade():
    op.create_table("style", ...)
    # 启用 RLS
    op.execute(rls_template("style"))  # 生成 ENABLE/FORCE/POLICY
```

### 5.4 索引规约
- 所有 tenant_id 列建立索引（自动通过 TenantScopedModel）
- 唯一约束都带 tenant_id 作为复合键

---

## 6. 共享 Celery 队列

| 队列 | 用途 | 拥有者单元 |
|---|---|---|
| `default` | 通用 | U01 |
| `backup` | 备份相关 | U01 |
| `wecom` | 企微调用 | U07 启用 |
| `crawler` | 采集相关 | U13 启用 |
| `monitor` | 监控告警 | U15 启用 |
| `report` | 报表预聚合 | U14 启用 |
| `ai` | AI 调用 | U18 启用 |

新队列在对应单元的 Infrastructure Design 中声明，不重新设计基础设施。

---

## 7. 共享 Sentry tag 规约

| Tag | 用途 | 来源 |
|---|---|---|
| `environment` | production / staging | ENVIRONMENT 环境变量 |
| `tenant_id` | 租户标识 | TenancyContext 中间件 |
| `actor_type` | user / system / worker / platform_admin | 同上 |

新单元如有特殊维度，可加新 tag（如 U13 加 `crawler_platform=qianniu`），但不修改已有 tag 含义。

---

## 8. 共享 R2 路径规约

| 桶 | 路径模板 | 来源单元 |
|---|---|---|
| public | `{tenant_id}/styles/{style_id}/{filename}` | U02, U03 |
| public | `{tenant_id}/designs/{style_id}/{filename}` | U10a |
| private | `{tenant_id}/settlements/{settlement_id}/proof/{uuid}.png` | U05 |
| private | `{tenant_id}/imports/{batch_id}/{filename}` | U06a |
| private | `{tenant_id}/patterns/{style_id}/{filename}` | U10a |
| credentials | `{tenant_id}/credentials/{credential_id}/{version}.bin` | U12 |
| backups | `daily/{YYYY-MM-DD}/daily-{YYYY-MM-DD}.tar.gz` | U01 |
| backups | `monthly/{YYYY-MM}/monthly-{YYYY-MM}.tar.gz` | U01 |
| backups | `audit-archive/{tenant_id}/{YYYY-MM}.jsonl.gz` | U01 |

staging 在所有路径前加 `staging/` 前缀。

---

## 9. 共享 GitHub Actions workflows

| Workflow | 触发 | 用途 |
|---|---|---|
| `ci.yml` | 所有 PR + push | lint + test + 覆盖率检查 |
| `migrate.yml` | 手动 `workflow_dispatch` | Alembic 迁移（每次有 schema 变更前手动触发） |
| `deploy-staging.yml` | `staging` 分支推送 | 部署 staging |
| `deploy-prod.yml` | `main` 分支推送 | 部署 production |

后续单元如需新 workflow（如 U13 的 RPA Worker 镜像构建），新建文件，不修改基础 workflows。

---

## 10. 后续单元的 Infrastructure Design 写作规约

新单元的 Infrastructure Design 文档应该：
1. **引用本文档**：`> 共享基础设施见 aidlc-docs/construction/shared-infrastructure.md`
2. **只列新增**：仅描述本单元新增的资源（新 R2 子路径 / 新 Celery 队列 / 新外部 API 集成 / 新环境变量）
3. **不重复 U01 内容**：不再列 6 服务 / Dockerfile / DNS 等

例如 U07（企微集成）的 Infrastructure Design 应该只写：
- 新增企微 API 凭据环境变量（WECOM_CORP_ID / WECOM_SECRET / WECOM_AGENT_ID）
- 新增 Celery 队列 `wecom`
- 新增企微回调端点路径（`POST /api/wecom/callback`，需要 Zeabur 域名继续暴露）

---

## 11. 一致性校验

| 校验 | 结果 |
|---|---|
| U01 已完成的基础设施都列在本文档 | ✅ |
| 后续单元拥有清晰的"扩展协议"（不重复定义基础） | ✅ |
| 多租户/审计/状态机等横切组件已声明 | ✅ |
| Celery 队列/R2 路径/Sentry tag 命名规范统一 | ✅ |
| 数据库迁移命名约定明确 | ✅ |
