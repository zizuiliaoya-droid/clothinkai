# U01 API 端点摘要

> 13 个端点（不含 /health, /ready, /metrics, /api/docs 等基础设施端点）。

| # | 方法 | 路径 | 故事 | 权限 | Schema (request) | Schema (response) |
|---|---|---|---|---|---|---|
| 1 | POST | `/api/auth/login` | EP01-S01 | 公开（IP 限流 20/min） | `LoginRequest` | `TokenPair` |
| 2 | POST | `/api/auth/refresh` | EP01-S01 | 公开 | `RefreshRequest` | `TokenPair` |
| 3 | POST | `/api/auth/logout` | — | 已登录 | — | 204 |
| 4 | GET | `/api/auth/me` | — | 已登录 | — | `UserSummary` |
| 5 | PUT | `/api/auth/password` | EP01-S02 | 已登录（含 must_change 状态） | `ChangePasswordRequest` | 204 |
| 6 | POST | `/api/users/` | EP01-S03 | `auth.user:write` | `UserCreate` | `UserCreateResponse`（含一次性临时密码） |
| 7 | GET | `/api/users/` | EP01-S03 | `auth.user:read` | query: `page,page_size,status,search` | `UserListResponse` |
| 8 | PUT | `/api/users/{id}` | EP01-S03 | `auth.user:write` | `UserUpdate` | `UserSummary` |
| 9 | PUT | `/api/users/{id}/toggle` | EP01-S03 | `auth.user:write` | — | `UserSummary` |
| 10 | PUT | `/api/users/{id}/unlock` | — | `auth.user:write` | — | `UserSummary` |
| 11 | PUT | `/api/users/{id}/reset-password` | EP01-S03 | `auth.user:write` | — | `ResetPasswordResponse`（含一次性临时密码） |
| 12 | POST | `/api/users/{id}/roles` | EP01-S04 | `auth.role:assign` | `RoleAssignRequest` | `UserSummary` |
| 13 | GET | `/api/audit-logs` | EP01-S08 | `auth.audit:read` | query: `action,resource,user_id,date_from,date_to,page,page_size` | `AuditLogListResponse` |

## 错误响应统一格式

```json
{
  "code": "ERROR_CODE",
  "message": "Human-readable message",
  "details": { "..." }
}
```

常见错误码：
- `INVALID_CREDENTIALS` (401)
- `TOKEN_INVALID` / `TOKEN_EXPIRED` (401)
- `ACCOUNT_DISABLED` (401)
- `ACCOUNT_LOCKED` (423)
- `PASSWORD_MUST_CHANGE` (423)
- `PERMISSION_DENIED` (403)
- `RATE_LIMITED` (429)
- `RESOURCE_NOT_FOUND` (404)
- `DUPLICATE_RESOURCE` / `USERNAME_ALREADY_EXISTS` (409)
- `VALIDATION_ERROR` / `WEAK_PASSWORD` (422)
- `TENANT_CONTEXT_MISSING` / `TENANT_CONTEXT_MISMATCH` (500)
- `ILLEGAL_STATE_TRANSITION` (422)
- `INTERNAL_ERROR` (500)

## 健康检查端点（不在 OpenAPI）

| 路径 | 用途 | Zeabur 配置 |
|---|---|---|
| GET `/health` | Liveness（永远 200） | （未配置） |
| GET `/ready` | Readiness（DB+Redis 健康才 200，否则 503） | health check 30s |
| GET `/metrics` | Prometheus 格式 | 内部（不暴露公网） |
| GET `/api/docs` | Swagger UI | OpenAPI 浏览 |
| GET `/api/redoc` | ReDoc | OpenAPI 浏览 |
| GET `/api/openapi.json` | OpenAPI 规范 | 工具消费 |
