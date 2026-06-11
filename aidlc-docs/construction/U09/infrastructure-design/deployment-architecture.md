# U09 部署架构（Deployment Architecture）

> 单元：U09 — 字段级权限 + 自定义权限
> 结论：无新服务/拓扑变更；部署 = 代码 + migration 012 同批；本文档给出 checklist + 验证 + 回滚

---

## 1. 部署拓扑（无变更）

```
[frontend] [backend] [celery-worker] [celery-beat] [postgres] [redis]
                ▲ U09 仅改动 backend 代码 + migration 012（permission seed）
```
- U09 不引入新服务、不改服务规格、不改网络/域名/TLS。

## 2. 部署 checklist

| # | 步骤 | 说明 |
|---|---|---|
| 1 | 合并 U09 代码到 main | core/security/field_permissions.py + PermissionService + 4 模块重构 + 删 4 legacy |
| 2 | migrate.yml 执行 `alembic upgrade head` | 应用 migration 012（18 字段 scope seed，幂等） |
| 3 | backend 自动部署（main → prod） | 复用既有 deploy-prod.yml |
| 4 | 冒烟验证（见 §3） | effective-permissions 端点 + 4 模块字段回归 |

- migration 与代码顺序：先 migrate（INSERT scope）再发布代码，或同批；因 seed 不绑角色且默认按注册表判定，先后顺序无回填风险。

## 3. 部署后验证

| 验证 | 方法 | 期望 |
|---|---|---|
| 字段 scope 已 seed | `SELECT count(*) FROM permission WHERE category='field'` | = 18 |
| grant/revoke API | admin 调 `POST /users/{id}/permissions/grant {scope:"field.sku.cost_price:read"}` | 200 {"ok":true} |
| effective-permissions | `GET /users/{id}/effective-permissions` | 含 role_scopes/grants/revokes/effective |
| 非 admin 鉴权 | 普通用户调 grant | 403 |
| 未知 scope | grant `{scope:"field.xxx.yyy:read"}` | 422 |
| sku.cost_price 回归 | 设计师查 SKU | 响应不含 cost_price 字段 |
| blogger.wechat 侧信道 | 无读权限用户 keyword 搜 wechat | 不命中（字段不参与匹配） |
| settlement.payment_amount 写 | 无写权限改 payment_amount | 403 FIELD_PERMISSION_DENIED |

## 4. 回滚步骤

| 场景 | 操作 |
|---|---|
| 代码问题 | 回滚 backend 到上一版本（恢复 4 legacy 行为；注册表值与 legacy 一致，无功能差异） |
| migration 问题 | `alembic downgrade -1`（删除 18 字段 scope + 引用 override 级联清理） |
| 缓存脏 | 自然过期（PERM_CACHE_TTL 5min）或手动 flush `perm:user:*` + `fieldctx:user:*` |

- 回滚安全：seed 为纯 INSERT；downgrade 为 DELETE；均不影响其他 permission/role_permission 行。

## 5. 本地验证

```bash
# Docker PG16 + Redis7（端口见 Build & Test 阶段，U09 用 5551/6406）
alembic upgrade head            # 应用 001→012
pytest tests/unit/test_field_permissions.py \
       tests/integration/test_custom_permission.py \
       tests/integration/test_field_perm_regression.py \
       tests/api/test_permission_api.py -p no:postgresql -m "not rls and not performance"
```

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新服务/拓扑变更 | ✅ §1 |
| 部署 = 代码 + migration 012 同批 | ✅ §2 |
| 验证覆盖 API + 4 模块回归 | ✅ §3 |
| 回滚无数据迁移风险 | ✅ §4 |
| 本地 Docker 验证步骤 | ✅ §5 |
