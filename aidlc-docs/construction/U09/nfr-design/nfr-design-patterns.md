# U09 NFR 设计模式（NFR Design Patterns）

> 单元：U09 — 字段级权限 + 自定义权限
> 增量模式：P-U09-01（字段权限注册表 + 判定 + 过滤）、P-U09-02（自定义权限 API + 复用合并）
> 继承：U01 merge_permissions / EffectivePermissions / Redis 权限缓存 / 全局 error handler

---

## P-U09-01 — 字段权限注册表 + can_read/write_field + field_filter

### 目的
统一 4 个 legacy_field_permissions（角色硬编码）为单一 core 注册表，叠加字段级自定义
override（撤销 > 授予 > 角色默认），过滤响应字段 / 守卫写入。

### 注册表（进程级常量，import 时构建，零运行时开销）

```python
# core/security/field_permissions.py
from dataclasses import dataclass

@dataclass(frozen=True)
class FieldRule:
    visible_roles: frozenset[str]
    writable_roles: frozenset[str] = frozenset()

FIELD_PERMISSION_REGISTRY: dict[str, dict[str, FieldRule]] = {
    "sku": {
        "cost_price":     FieldRule(frozenset({"admin","merchandiser","finance"}),
                                    frozenset({"admin","merchandiser","finance"})),
        "purchase_price": FieldRule(frozenset({"admin","merchandiser","finance"}),
                                    frozenset({"admin","merchandiser","finance"})),
    },
    "blogger": {
        "quote":  FieldRule(frozenset({"admin","pr","pr_manager","finance"}),
                            frozenset({"admin","pr","pr_manager"})),
        "wechat": FieldRule(frozenset({"admin","pr","pr_manager"}),
                            frozenset({"admin","pr","pr_manager"})),
        "phone":  FieldRule(frozenset({"admin","pr","pr_manager"}),
                            frozenset({"admin","pr","pr_manager"})),
    },
    "promotion": {
        "quote_amount":  FieldRule(frozenset({"admin","pr","pr_manager","finance"}),
                                   frozenset({"admin","pr","pr_manager"})),
        "cost_snapshot": FieldRule(frozenset({"admin","pr","pr_manager","finance"}),
                                   frozenset({"admin","pr","pr_manager"})),
    },
    "settlement": {
        "amount":         FieldRule(frozenset({"admin","pr_manager","finance"})),
        "total_amount":   FieldRule(frozenset({"admin","pr_manager","finance"})),
        "payment_amount": FieldRule(frozenset({"admin","pr_manager","finance"}),
                                    frozenset({"admin","pr_manager"})),
    },
}
```

### FieldPermissionContext + 判定函数

```python
@dataclass(frozen=True)
class FieldPermissionContext:
    role_codes: frozenset[str]   # 角色 code（admin/merchandiser/pr/...）
    grants: frozenset[str]       # field.* 前缀的自定义授予 scope
    revokes: frozenset[str]      # field.* 前缀的自定义撤销 scope
    is_superuser: bool           # '*' in merged_scopes

def can_read_field(entity: str, field: str, ctx: FieldPermissionContext) -> bool:
    rule = FIELD_PERMISSION_REGISTRY.get(entity, {}).get(field)
    if rule is None or ctx.is_superuser:   # 不在注册表 → 无限制；超管通配
        return True
    scope = f"field.{entity}.{field}:read"
    if scope in ctx.revokes:               # 撤销优先级最高
        return False
    if scope in ctx.grants:                # 自定义授予
        return True
    return bool(ctx.role_codes & rule.visible_roles)   # 角色默认

def can_write_field(entity: str, field: str, ctx: FieldPermissionContext) -> bool:
    rule = FIELD_PERMISSION_REGISTRY.get(entity, {}).get(field)
    if rule is None or ctx.is_superuser:
        return True
    scope = f"field.{entity}.{field}:write"
    if scope in ctx.revokes:
        return False
    if scope in ctx.grants:
        return True
    return bool(ctx.role_codes & rule.writable_roles)

def field_filter(entity: str, data: dict, ctx: FieldPermissionContext) -> dict:
    """从响应 dict 移除不可读字段（移除而非置 null，防存在性泄露）。"""
    for field in FIELD_PERMISSION_REGISTRY.get(entity, {}):
        if field in data and not can_read_field(entity, field, ctx):
            data.pop(field)
    return data
```

### ctx 构造（service 入口 / deps）

```python
async def build_field_perm_context(user_id, repo, perms) -> FieldPermissionContext:
    role_codes = frozenset(await repo.list_codes_for_user(user_id))   # 复用 U01
    role_scopes, grants, revokes = await repo.list_scopes_for_user(user_id)
    field_grants  = frozenset(s for s in grants  if s.startswith("field."))
    field_revokes = frozenset(s for s in revokes if s.startswith("field."))
    return FieldPermissionContext(
        role_codes=role_codes,
        grants=field_grants,
        revokes=field_revokes,
        is_superuser=("*" in perms.scopes),
    )
```

- 性能：注册表常量 + set 交集 = O(字段数)，无额外 DB；ctx 构造复用既有 1-2 次查询（可 Redis 缓存 `fieldctx:user:<id>`，TTL=PERM_CACHE_TTL）。
- 安全（keyword 侧信道，blogger 搜索）：

```python
conds = [Blogger.name.ilike(f"%{kw}%")]
if can_read_field("blogger", "wechat", ctx):   # 无读权限 → 不参与匹配
    conds.append(Blogger.wechat.ilike(f"%{kw}%"))
stmt = stmt.where(or_(*conds))
```

- 写守卫：service 写入前对 payload 中**显式出现**的注册表字段调用 `can_write_field`，否则
  `raise FieldPermissionDenied(entity, field)`（403）。

### FieldPermissionDenied（移至 core/exceptions.py）

```python
class FieldPermissionDenied(AppException):
    code = "FIELD_PERMISSION_DENIED"
    http_status = 403
    def __init__(self, entity: str, field: str):
        super().__init__(
            f"无字段写权限：{entity}.{field}",
            details={"entity": entity, "field": field},
        )
```
删除 product/blogger/promotion/finance 各自重复的 FieldPermissionDenied 定义，统一导入 core。

---

## P-U09-02 — 自定义权限 API（grant / revoke / effective-permissions）

### 目的
EP01-S05 自定义权限底层（user_permission_override + merge_permissions + list_scopes_for_user）
U01 已就绪；本模式仅补 **3 个 API + PermissionService**，不重写合并逻辑。

### PermissionService（modules/auth/service.py 追加或新建 permission_service）

```python
class PermissionService:
    def __init__(self, session):
        self._session = session
        self._perms = PermissionRepository(session)
        self._users = UserRepository(session)

    async def grant(self, target_user_id, scope, *, actor_id, reason) -> None:
        await self._validate_scope_exists(scope)          # permission 表存在校验 → 422
        await self._perms.upsert_override(target_user_id, scope, effect="grant",
                                          created_by=actor_id, reason=reason)
        await invalidate_user_permissions_cache(str(target_user_id))
        await self._cache_invalidate_field_ctx(target_user_id)
        await audit_log("permission.grant", target=target_user_id,
                        detail={"scope": scope, "effect": "grant"})

    async def revoke(self, target_user_id, scope, *, actor_id, reason) -> None:
        await self._validate_scope_exists(scope)
        await self._perms.upsert_override(target_user_id, scope, effect="revoke",
                                          created_by=actor_id, reason=reason)
        await invalidate_user_permissions_cache(str(target_user_id))
        await self._cache_invalidate_field_ctx(target_user_id)
        await audit_log("permission.revoke", target=target_user_id,
                        detail={"scope": scope, "effect": "revoke"})

    async def get_effective(self, target_user_id) -> EffectivePermissionsView:
        role_scopes, grants, revokes = await self._perms.list_scopes_for_user(target_user_id)
        effective = merge_permissions(role_scopes, grants, revokes)   # 复用 U01
        return EffectivePermissionsView(
            user_id=str(target_user_id),
            role_scopes=sorted(role_scopes),
            grants=sorted(grants), revokes=sorted(revokes),
            effective=sorted(effective),
        )
```

- `upsert_override`：(tenant, user, permission_id, effect) UNIQUE 冲突 → 更新 effect/reason（同 scope 改授予/撤销可切换）。
- 缓存：同时失效 perm cache + field ctx cache。

### 3 个 API 端点（modules/auth/api.py）

```python
@router.post("/users/{user_id}/permissions/grant",
             dependencies=[Depends(require_permission("auth.permission","grant"))])
async def grant_permission(user_id, body: PermissionOverrideIn, ...): ...   # 204→200 {"ok":True}

@router.post("/users/{user_id}/permissions/revoke",
             dependencies=[Depends(require_permission("auth.permission","grant"))])
async def revoke_permission(user_id, body: PermissionOverrideIn, ...): ...

@router.get("/users/{user_id}/effective-permissions",
            dependencies=[Depends(require_permission("auth.permission","grant"))])
async def effective_permissions(user_id, ...) -> EffectivePermissionsView: ...
```

- 鉴权：`auth.permission:grant`（admin 默认持有，migration 012 不绑角色但 admin 持 `*` 通配）。
- 未知 scope（不在 permission 表）→ 422（防止误授予拼写错误的 scope）。
- 返回体遵循 U07 教训：不用 204（避免 app 构造问题），grant/revoke 返回 200 `{"ok": True}`。

---

## 模式与 NFR 映射

| 模式 | NFR 目标 |
|---|---|
| P-U09-01 | 内存过滤 O(字段数) + 移除非 null + keyword 侧信道 + 撤销>授予>角色 + 回归兼容 |
| P-U09-02 | grant/revoke ≤150ms + effective ≤200ms + 缓存失效一致 + audit + 鉴权 |

## 一致性校验

| 校验 | 结果 |
|---|---|
| 注册表值迁移自 4 legacy（行为兼容） | ✅ P-U09-01 |
| can_read/write_field 撤销>授予>角色 + admin 通配 | ✅ P-U09-01 |
| field_filter 移除字段防存在性泄露 | ✅ P-U09-01 |
| keyword 侧信道 wechat 受 can_read 控制 | ✅ P-U09-01 |
| 复用 merge_permissions + 缓存双失效 | ✅ P-U09-02 |
| grant/revoke/effective 鉴权 + audit + 未知 scope 422 | ✅ P-U09-02 |
| proof_upload 改动作 scope finance.settlement:pay | ✅ Q7 |
