# U01 测试覆盖映射

> 故事 GWT 验收标准 → 测试用例的映射。

---

## 1. 单元测试

| 文件 | 覆盖 BR | 测试用例数 |
|---|---|---|
| `tests/unit/test_permissions.py` | BR-PERM-001（撤销 > 授予 > 角色） + 通配符匹配 | 8 |
| `tests/unit/test_password_policy.py` | BR-PWD-001（4 条强度规则） + BR-PWD-004（临时密码生成） | 9 |
| `tests/unit/test_state_machine.py` | StateMachine 基类（U03/U04/U05/U10a 复用） | 7 |

## 2. 集成测试（依赖 PostgreSQL）

### test_auth_login.py — EP01-S01

| GWT | 用例 |
|---|---|
| Given 用户名密码正确 / When 登录 / Then 返回 access+refresh | `test_login_success` |
| Given 密码错误 / When 登录 / Then 401 | `test_login_invalid_password` |
| Given 用户不存在 / When 登录 / Then 401（不区分） | `test_login_unknown_user` |
| Given 5 次失败 / When 第 6 次 / Then 429 | `test_rate_limit_after_5_failures` |
| Given 10 次失败 / When 第 11 次（即使密码对） / Then 423 | `test_account_locked_after_10_failures` |
| Given 用户 disabled / When 登录 / Then 401 | `test_disabled_user_rejected` |

### test_auth_password.py — EP01-S02

| GWT | 用例 |
|---|---|
| Given 老密码正确 / When 修改 / Then 成功 + token 失效 | `test_change_success` + `test_pwd_iat_updated_after_change` + `test_refresh_tokens_revoked_after_change` |
| Given 老密码错 / When 修改 / Then 401 | `test_wrong_old_password` |
| Given 新密码 = 老密码 / When 修改 / Then 拒绝 | `test_same_as_current` |
| Given 弱密码 / When 提交 / Then schema 422 | `test_weak_password_rejected_at_schema_layer` |

### test_user_management.py — EP01-S03/S04

| GWT | 用例 |
|---|---|
| Given 管理员创建用户 / When POST /users / Then 创建 + 返回临时密码 | `test_create_with_initial_password` |
| Given 用户名重复 / When 创建 / Then 409 | `test_create_duplicate_username` |
| Given 角色不存在 / When 创建 / Then RoleNotFoundError | `test_create_with_invalid_role` |
| Given 用户启用 / When toggle / Then 状态变为 disabled | `test_toggle_active_to_disabled` |
| Given 用户已锁定 / When unlock / Then 解锁成功 | `test_unlock_locked_user` |
| Given 用户未锁定 / When unlock / Then 422 | `test_unlock_not_locked_user_raises` |
| Given 用户分配多角色 / When 重新分配 / Then 替换为新集合 | `test_assign_roles` |

### test_tenant_isolation.py — EP01-S07

| GWT | 用例 |
|---|---|
| Given 租户 A/B 各有用户 / When 切到 A 上下文查询 / Then 仅看到 A 数据 | `test_orm_query_filters_by_tenant` |
| Given INSERT 未填 tenant_id / When flush / Then 自动填上下文 tenant_id | `test_insert_auto_fills_tenant_id` |
| Given INSERT tenant_id 与 ctx 不匹配 / When flush / Then TenantContextMismatchError | `test_insert_with_mismatched_tenant_raises` |
| Given 同 username 在不同租户 / When 创建 / Then 都成功（约束 (tenant_id, username)） | `test_unique_constraint_per_tenant` |

### test_rls.py — PostgreSQL RLS（需 TEST_DATABASE_URL_APP 环境变量）

| GWT | 用例 |
|---|---|
| Given 用 clothing_app 角色 + SET LOCAL tenant_id / When 直接 SQL / Then 仅看到该租户数据 | `test_rls_filters_user_table` |
| Given SET LOCAL bypass_rls = on / When 直接 SQL / Then 跨租户可见 | `test_bypass_rls_sees_all` |
| Given clothing_app 角色 / When UPDATE audit_log / Then 失败（REVOKE） | `test_clothing_app_cannot_update_audit_log` |
| Given clothing_app 角色 / When DELETE audit_log / Then 失败 | `test_clothing_app_cannot_delete_audit_log` |

### test_audit_log.py — EP01-S08

| GWT | 用例 |
|---|---|
| Given AuditService.log / When 提交 / Then audit_log 表新增一条 | `test_log_writes_record` |
| Given contextvars 中有 user_id/tenant_id/actor_type / When log() / Then 自动读取 | `test_log_uses_context_when_actor_not_provided` |
| Given audit_log 多条记录 / When 按 action 查询 / Then 仅返回匹配项 | `test_query_filters_combined` |

## 3. API 测试

### test_health.py — /health & /ready

| GWT | 用例 |
|---|---|
| Given 任何状态 / When GET /health / Then 200 | `test_health_returns_200` |
| Given DB+Redis 健康 / When GET /ready / Then 200 | `test_ready_when_all_healthy` |
| Given DB unreachable / When GET /ready / Then 503 + checks.db=error | `test_ready_when_db_unhealthy` |
| Given X-Request-ID header / When 任意请求 / Then 响应回显 | `test_request_id_header_echoed` |

### test_auth_api.py — 端点契约

| GWT | 用例 |
|---|---|
| Given 缺 username / When POST /login / Then 422 + VALIDATION_ERROR | `test_login_endpoint_validates_payload` |
| Given 弱密码 / When PUT /password / Then 401 或 422（取决于鉴权链路） | `test_change_password_validates_strength` |
| Given 无 token / When GET /users / Then 401 | `test_protected_endpoint_requires_auth` |
| Given 无 token / When GET /audit-logs / Then 401 | `test_audit_logs_requires_auth` |
| Given 应用启动 / When GET /api/openapi.json / Then 暴露所有端点 | `test_openapi_schema_exposed` |
| 错误响应统一格式 `{code, message, details}` | `test_404_format` + `test_validation_error_format` |

---

## 4. 覆盖率门槛

`pyproject.toml` 配置 `--cov-fail-under=70`，CI 强制：
- Domain 层目标 ≥ 90%
- Service 层目标 ≥ 80%
- 整体 ≥ 70%（CI 强制）+ 80%（验收目标）

CI 不跑 RLS 测试（需 clothing_app 真实角色，本地或 staging 跑 `pytest -m rls`）。

---

## 5. 跨阶段测试承诺

- **MVP 末**：所有 U01-U08 单元的集成测试通过；多租户隔离回归
- **V1 末**：U09 字段级权限上线后，回归所有 MVP API 的字段屏蔽
- **每季度**：执行 `scripts/restore_backup.py` 演练
