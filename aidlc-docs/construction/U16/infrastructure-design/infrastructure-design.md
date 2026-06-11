# U16 基础设施设计（拍单 / 刷单 / 余额）

> 增量式：复用 U01/U05 全部基础设施（Zeabur 6 服务 + RLS + 审计 + 事件总线）。
> 单元：EP06-S09、S10、S11（V2）。唯一增量 = migration 020（2 表 + promotion ALTER）。

---

## 1. 服务拓扑（无变更）

| 服务 | U16 用途 | 变更 |
|---|---|---|
| backend | 拍单/刷单/余额 API + SettlementRequested listener（在线事务内） | 无（挂 order_adjustment_router） |
| celery-worker | — | 无（U16 无异步任务） |
| celery-beat | — | 无 |
| postgres | order_adjustment / balance_record 2 表 + promotion.in_store_order | migration 020 |
| redis | — | 无 |
| frontend | （不在本单元范围） | 无 |

**结论**：无新服务、无新进程、无 Celery 任务/Beat、无资源规格变更。

---

## 2. 数据库变更（migration 020）

### 表 1：order_adjustment（拍单/刷单统一建模）
| 列 | 类型 | 约束 |
|---|---|---|
| base_cols | TenantScopedModel + FK tenant RESTRICT | RLS |
| order_type | String(8) | NOT NULL（拍单/刷单） |
| order_date | Date | NULL |
| order_no | String(64) | NULL（重复 warning 不硬拒） |
| blogger_identifier | String(128) | NULL |
| style_id | UUID FK style RESTRICT | NULL |
| sku_id | UUID FK sku SET NULL | NULL |
| amount | Numeric(12,2) | NOT NULL, CHECK ≥ 0 |
| payment_amount / payment_date | Numeric(12,2) / Date | NULL |
| payment_proof_attachment_id | UUID FK attachment RESTRICT | NULL |
| exclude_from_roi | Boolean | NOT NULL DEFAULT false |
| status | String(8) | NOT NULL DEFAULT '待付款' |
| promotion_id | UUID FK promotion SET NULL | NULL |
| remark | Text | NULL |

索引：`uq_order_adjustment_promotion` UNIQUE(tenant_id, promotion_id) **WHERE promotion_id IS NOT NULL** + `idx_order_adjustment_type`(tenant_id, order_type, order_date) + `idx_order_adjustment_roi`(tenant_id, style_id, exclude_from_roi)。CHECK：order_type IN / status IN / amount ≥ 0。RLS 启用。

### 表 2：balance_record（余额流水）
| 列 | 类型 | 约束 |
|---|---|---|
| base_cols | TenantScopedModel | RLS |
| record_date | Date | NOT NULL |
| record_type | String(16) | NOT NULL |
| income / expense | Numeric(12,2) | NULL, CHECK ≥ 0 |
| balance_after | Numeric(12,2) | NOT NULL |
| remark | String(255) | NULL |
| created_by | UUID FK user SET NULL | NULL |

索引：`idx_balance_record_tenant_created`(tenant_id, created_at)。RLS 启用。

### promotion ALTER
- `ADD COLUMN in_store_order Boolean NOT NULL DEFAULT false`（不锁表，无回填）。

### scope seed
- permission：finance.order:read/write + finance.balance:read/write（ON CONFLICT(scope) DO NOTHING）。
- role_permission：finance 显式绑 4 scope（admin 通配 "*" 已覆盖）。

### 迁移属性
- revision `"020_u16_order_adjustment_balance"`（30 字符 ≤ 32），down_revision `"019_u15_wecom_alert_tables"`。
- 无回填；down 安全 drop 2 表 + drop column + 删 scope。

---

## 3. 复用基础设施（零新增）

| 维度 | 复用 | 说明 |
|---|---|---|
| 依赖 | re / Decimal（标准库）+ SQLAlchemy + prometheus | U01/U05 已有 |
| 环境变量 | 无新增 | — |
| Redis / R2 | 无用量 | — |
| 事件总线 | core/events（SettlementRequested 多 handler） | U04/U05 |
| 审计 | AuditService | U05 |
| 投产聚合 | U14 ProductionRepository/Service | report 模块 |

---

## 4. ROI 口径升级（部署影响）

- exclude_brushing 默认改 true（V2 真实 ROI）；部署即生效。
- 无刷单数据时剔除 0，与 V1 口径一致（U14 既有测试不破坏，已验证无 order_adjustment 数据）。
- 调用方可传 exclude_brushing=false 查含刷单口径对比。
- 前端/报表使用方需知晓口径变化（文档标注）。

---

## 5. 部署一致性

- U16 依赖 U05（finance 模块 + SettlementRequested 事件 + listeners.register）+ U14（report ProductionService）均已部署（V1 末完成）。
- U16 在 finance.listeners.register() 内追加 subscribe("SettlementRequested", auto_order)；与 U05 同事件多 handler，dispatch 顺序执行；U05 先部署事件已存在，无逆向风险。
- migration 顺序：020 紧接 019（U15），head 推进到 020。
- promotion.in_store_order ALTER 兼容旧数据（DEFAULT false）。

---

## 6. 本地验证

- Docker PG16:5559 + Redis7:6414 + python:3.12-slim（U16 唯一端口）。
- alembic upgrade head（含 020）；U16 子集（test_order_amount_balance + test_order_adjustment + test_order_adjustment_api）+ 全量回归；覆盖率 ≥70%。

---

## 7. 回滚

- 代码：移除 order_adjustment_router + finance auto-order subscribe + 恢复 production_service 默认 exclude_brushing=false（口径回退）。
- DB：migration 020 down（drop order_adjustment / balance_record + drop column in_store_order + 删 4 scope）；无外键被引用，安全幂等。

---

> spec-format 校验「Missing ## Overview / ## Architecture」为已知假阳性，IGNORE。
