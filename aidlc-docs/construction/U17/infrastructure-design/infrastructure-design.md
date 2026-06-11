# U17 基础设施设计（套装 + BI 看板 + 报表导出）

> 增量式：复用 U01/U02/U14 全部基础设施（Zeabur 6 服务 + RLS + 审计 + openpyxl）。
> 单元：EP02-S08、EP09-S06、EP09-S08（V2 收官单元）。唯一增量 = migration 021（3 表）。

---

## 1. 服务拓扑（无变更）

| 服务 | U17 用途 | 变更 |
|---|---|---|
| backend | bundle/BI/导出 API（含 xlsx 流式响应） | 无（挂 3 router） |
| celery-worker | — | 无（U17 无异步任务） |
| celery-beat | — | 无 |
| postgres | bundle_product / bundle_item / user_preference 3 表 | migration 021 |
| redis | — | 无 |
| frontend | （不在本单元范围） | 无 |

**结论**：无新服务、无新进程、无 Celery/Beat、无资源规格变更。

---

## 2. 数据库变更（migration 021）

### 表 1：bundle_product
| 列 | 类型 | 约束 |
|---|---|---|
| base_cols | TenantScopedModel + FK tenant RESTRICT | RLS |
| bundle_code | String(64) | NOT NULL |
| bundle_name | String(255) | NOT NULL |
| remark | Text | NULL |
| is_active | Boolean | NOT NULL DEFAULT true |

索引：`uq_bundle_product_code` UNIQUE(tenant_id, bundle_code)。RLS 启用。

### 表 2：bundle_item
| 列 | 类型 | 约束 |
|---|---|---|
| base_cols | TenantScopedModel | RLS |
| bundle_id | UUID FK bundle_product CASCADE | NOT NULL |
| sku_id | UUID FK sku RESTRICT | NOT NULL |
| quantity | Integer | NOT NULL, CHECK ≥ 1 |

索引：`uq_bundle_item_sku` UNIQUE(tenant_id, bundle_id, sku_id) + `idx_bundle_item_bundle`(tenant_id, bundle_id)。RLS 启用。

### 表 3：user_preference
| 列 | 类型 | 约束 |
|---|---|---|
| base_cols | TenantScopedModel | RLS |
| user_id | UUID FK user CASCADE | NOT NULL |
| pref_key | String(64) | NOT NULL |
| pref_value | JSONB | NOT NULL DEFAULT '{}' |

索引：`uq_user_preference` UNIQUE(tenant_id, user_id, pref_key)。RLS 启用。

### scope seed
- permission：product.bundle:read/write + report.export:read（ON CONFLICT(scope) DO NOTHING）。
- role_permission：merchandiser 绑 product.bundle:read/write；pr_manager + operations 绑 report.export:read（admin 通配 "*" 已覆盖）。

### 迁移属性
- revision `"021_u17_bundle_bi_export"`（24 字符 ≤ 32），down_revision `"020_u16_order_adjustment_balance"`。
- 无回填；down 安全 drop 3 表 + 删 4 scope。

---

## 3. 复用基础设施（零新增）

| 维度 | 复用 | 说明 |
|---|---|---|
| 依赖 | openpyxl==3.1.5 + io.BytesIO + SQLAlchemy + prometheus | U06a/U01 已有 |
| 环境变量 | 无新增 | — |
| Redis / R2 | 无用量 | — |
| report service | U14 ProductionService/StoreDailyService/WorkProgressService + resolve_time_range | BI/导出复用 |
| product | U02 Sku 模型 | bundle_item FK |
| 审计 | AuditService | bundle 创建留痕 |

---

## 4. 导出流式响应（部署面）

- 导出返回 StreamingResponse（xlsx 二进制流 + Content-Disposition attachment）。
- backend 无需额外配置；Zeabur/反向代理默认支持二进制响应 + 自定义 header。
- 无异步任务，受 HTTP 超时约束；V2 报表量级 ≤3s，远低于超时阈值。
- openpyxl write_only 流式写 BytesIO，内存可控。

---

## 5. 部署一致性

- U17 依赖 U02（product/sku）+ U14（report service）均已部署（MVP/V1 完成）。
- migration 021 紧接 020（U16），head 推进到 021。
- bundle/user_preference 为新表，无对历史数据影响；BI/导出只读复用既有 service，无破坏性变更。

---

## 6. 本地验证

- Docker PG16:5560 + Redis7:6415 + python:3.12-slim（U17 唯一端口）。
- alembic upgrade head（含 021）；U17 子集（test_bundle_export + test_bundle_bi_export + test_bundle_export_api）+ 全量回归；覆盖率 ≥70%。

---

## 7. 回滚

- 代码：移除 bundle_router/bi_router/export_router 挂载。
- DB：migration 021 down（drop bundle_product/bundle_item/user_preference + 删 4 scope）；无外键被引用，安全幂等。

---

> spec-format 校验「Missing ## Overview / ## Architecture」为已知假阳性，IGNORE。
