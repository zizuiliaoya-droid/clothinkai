# U09 基础设施设计（Infrastructure Design）

> 单元：U09 — 字段级权限 + 自定义权限
> 结论：**零基础设施增量** —— 无新 Zeabur 服务 / 无新表 / 无新依赖 / 无新环境变量；唯一增量 = migration 012（permission seed）

---

## 1. 基础设施增量总览

| 维度 | 增量 |
|---|---|
| Zeabur 服务 | 无（复用 backend FastAPI） |
| 数据库表 | 无新表；migration 012 仅向 permission 表 INSERT 18 行字段 scope |
| 数据库扩展/索引 | 无 |
| 依赖（requirements） | 无 |
| 环境变量 / Secrets | 无 |
| Celery 队列/任务 | 无 |
| Redis | 复用既有 cache 库，新增 `fieldctx:user:<id>` key（同 TTL=PERM_CACHE_TTL） |
| R2 桶 | 无 |
| Prometheus 指标 | 无新增（复用 HTTP 4xx） |

---

## 2. migration 012（字段 scope permission seed）

```python
# alembic/versions/012_u09_seed_field_permissions.py
"""U09: seed 字段级权限 scope 定义（不绑角色）。接 011。"""
from alembic import op

revision = "012_u09_seed_field_permissions"
down_revision = "011_u07_create_wecom_tables"

FIELD_SCOPES = [
    ("field.sku.cost_price:read", "字段-SKU成本价-读"),
    ("field.sku.cost_price:write", "字段-SKU成本价-写"),
    ("field.sku.purchase_price:read", "字段-SKU采购价-读"),
    ("field.sku.purchase_price:write", "字段-SKU采购价-写"),
    ("field.blogger.quote:read", "字段-博主报价-读"),
    ("field.blogger.quote:write", "字段-博主报价-写"),
    ("field.blogger.wechat:read", "字段-博主微信-读"),
    ("field.blogger.wechat:write", "字段-博主微信-写"),
    ("field.blogger.phone:read", "字段-博主电话-读"),
    ("field.blogger.phone:write", "字段-博主电话-写"),
    ("field.promotion.quote_amount:read", "字段-推广报价-读"),
    ("field.promotion.quote_amount:write", "字段-推广报价-写"),
    ("field.promotion.cost_snapshot:read", "字段-推广成本快照-读"),
    ("field.promotion.cost_snapshot:write", "字段-推广成本快照-写"),
    ("field.settlement.amount:read", "字段-结算金额-读"),
    ("field.settlement.total_amount:read", "字段-结算总额-读"),
    ("field.settlement.payment_amount:read", "字段-结算实付-读"),
    ("field.settlement.payment_amount:write", "字段-结算实付-写"),
]

def upgrade() -> None:
    for scope, desc in FIELD_SCOPES:
        op.execute(
            "INSERT INTO permission (id, scope, description, category, created_at, updated_at) "
            "VALUES (gen_random_uuid(), '%s', '%s', 'field', now(), now()) "
            "ON CONFLICT (scope) DO NOTHING" % (scope, desc)
        )

def downgrade() -> None:
    scopes = ",".join("'%s'" % s for s, _ in FIELD_SCOPES)
    op.execute("DELETE FROM permission WHERE scope IN (%s)" % scopes)
```

> 说明：
> - 18 个字段 scope；settlement.amount/total_amount 仅 read（写由状态机控制）。
> - `ON CONFLICT (scope) DO NOTHING` 幂等，可重复执行。
> - **不写 role_permission**：默认字段权限按注册表角色判定；这些 scope 仅供自定义 grant/revoke 引用 + 存在性校验（防误授拼写错误 scope，422）。
> - 实际字段列名/类型需与 U01 permission 表定义对齐（id/scope/description/category/created_at/updated_at）；占位 SQL 在 Code Generation 阶段按真实模型微调（参数化绑定）。

---

## 3. Redis key 增量

| key | 用途 | TTL | 失效时机 |
|---|---|---|---|
| `fieldctx:user:<id>` | 缓存 FieldPermissionContext（role_codes + field grants/revokes + is_superuser） | PERM_CACHE_TTL（5min） | grant/revoke 后与 `perm:user:<id>` 一并失效 |

- 复用既有 cache 库，无新 Redis 实例。

---

## 4. 复用清单（U01-U08）

| 复用项 | 来源 |
|---|---|
| permission 表 / user_permission_override 表 | U01 |
| merge_permissions / list_scopes_for_user / list_codes_for_user | U01 |
| EffectivePermissions / invalidate_user_permissions_cache / cache | U01 |
| 全局 error handler（FieldPermissionDenied 403 序列化） | U01 |
| migrate.yml job / ci.yml | U01 |
| backend FastAPI 服务 | U01 |

---

## 5. 部署 / 回滚

- **部署**：代码 + migration 012 同批；migrate.yml `alembic upgrade head` 执行 012；无回填、无数据迁移。
- **回滚**：代码回滚即恢复 4 legacy 行为（注册表值与 legacy 完全一致）；migration 012 downgrade 删除 18 scope（连带清理引用这些 scope 的 override，按既有 FK）。
- **零停机**：seed 为纯 INSERT，不锁表、不阻塞既有查询。

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新服务/表/依赖/环境变量 | ✅ §1 |
| migration 012 仅 18 scope seed 幂等不绑角色 | ✅ §2 |
| Redis fieldctx key 复用既有库 + 同失效 | ✅ §3 |
| 复用 U01 权限基础设施 | ✅ §4 |
| 部署/回滚无回填风险 | ✅ §5 |

> 注：infrastructure-design.md 触发 spec-format 假阳性（Missing ## Overview/## Architecture 等）= 已知，IGNORE（AI-DLC 格式 ≠ Kiro spec 模板）。
